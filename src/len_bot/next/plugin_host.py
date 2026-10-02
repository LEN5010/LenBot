"""Load configured plugin packages and run them at the plugin error boundary (M14)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import inspect
import logging
from pathlib import Path
import re
from string import Template
import sys
import time
import tomllib
from typing import TYPE_CHECKING, Annotated, Literal, get_type_hints
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, JsonValue,
                      TypeAdapter, ValidationError, create_model, field_validator, model_validator)

from .config import PLUGIN_NAME, PLUGIN_RESERVED, HostConfig
from .external_tools import ExternalTool
from .messages import parse_notice, ChatMessage
from .plugin import (INTERFACE, MARK, Content, GenerationRole, Image, Text,
                     Invocation, Notice, Plugin, PluginContext, Sent)
from .plugin_kv import PluginKV
from .model import ChatModel, ModelReply
from .model_request import request_model
from .schedule_time import Cron, next_cron, parse_cron
from .skills import Skill, load_catalog, load_plugin_skills
from .store import encode

if TYPE_CHECKING:
    from .network import NetworkRuntime


logger = logging.getLogger(__name__)
BUILTIN = Path(__file__).with_name("builtin_plugins")
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
STRICT = ConfigDict(extra="forbid", strict=True)
FIELD_TYPES = {"string": str, "secret": str, "integer": int, "number": float, "boolean": bool,
               "string_list": list[str], "object_list": list[dict[str, JsonValue]]}
ERROR_LIMIT = 20


class ConfigItem(BaseModel):
    """One form value, also used for an object-list row's named children."""
    model_config = STRICT
    type: Literal["string", "integer", "number", "boolean", "string_list"]
    description: str = Field(min_length=1)
    default: str | int | float | bool | list[str] | list[dict[str, JsonValue]] | None = None
    options: list[str | int | float] | None = Field(default=None, min_length=1)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    def annotation(self):
        constraints = Field(ge=self.minimum, le=self.maximum) if self.type in {"integer", "number"} else Field()
        value_type = Annotated[FIELD_TYPES[self.type], constraints]
        if self.options is not None:
            def choice(value):
                if value not in self.options:
                    raise ValueError(f"必须是 {self.options!r} 中的一项")
                return value
            value_type = Annotated[value_type, AfterValidator(choice), Field(json_schema_extra={"enum": self.options})]
        return value_type

    @model_validator(mode="after")
    def valid_constraints(self):
        if (self.minimum is not None or self.maximum is not None) and self.type not in {"integer", "number"}:
            raise ValueError("minimum/maximum 只用于 integer 或 number")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum 不能大于 maximum")
        if self.options is not None:
            if self.type not in {"string", "integer", "number"}:
                raise ValueError("options 只用于 string、integer 或 number")
            adapter = TypeAdapter(FIELD_TYPES[self.type], config=STRICT)
            for option in self.options:
                adapter.validate_python(option)
        if "default" in self.model_fields_set:
            TypeAdapter(self.annotation(), config=STRICT).validate_python(self.default)
        return self


class ConfigField(ConfigItem):
    type: Literal["string", "secret", "integer", "number", "boolean", "string_list", "object_list"]
    fields: dict[str, ConfigItem] = Field(default_factory=dict)

    def annotation(self):
        if self.type == "object_list" and self.fields:
            return list[config_model("PluginConfigRow", self.fields)]
        return super().annotation()

    @model_validator(mode="after")
    def object_fields(self):
        if self.fields and self.type != "object_list":
            raise ValueError("fields 只用于 object_list")
        check_field_names(self.fields)
        return self


def check_field_names(fields: Mapping[str, ConfigItem]) -> None:
    for key in fields:
        if re.fullmatch(r"[a-z][a-z0-9_]*", key) is None:
            raise ValueError(f"配置字段 {key!r} 只能使用小写字母、数字和下划线")


def config_model(name: str, fields: Mapping[str, ConfigItem]) -> type[BaseModel]:
    return create_model(name, __config__=STRICT, **{
        key: (item.annotation(), Field(item.default if "default" in item.model_fields_set else ...,
                                       description=item.description, validate_default=True))
        for key, item in fields.items()
    })


class Manifest(BaseModel):
    model_config = STRICT
    name: str
    version: str = Field(min_length=1)
    interface: int
    authors: list[str] = Field(min_length=1)
    license: str = Field(min_length=1)
    description: str = Field(min_length=1)
    repository: HttpUrl | None = None
    homepage: HttpUrl | None = None
    config: dict[str, ConfigField] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if PLUGIN_NAME.fullmatch(value) is None or value in PLUGIN_RESERVED:
            raise ValueError("name must use lowercase letters, digits and underscores, and not be paths/data_directory")
        return value

    @field_validator("config")
    @classmethod
    def valid_fields(cls, value: dict[str, ConfigField]) -> dict[str, ConfigField]:
        check_field_names(value)
        return value

    def values_model(self) -> type[BaseModel]:
        return config_model(f"PluginConfig_{self.name}", self.config)


def redact_values(text: str, manifest: Manifest | None, values: Mapping[str, object]) -> str:
    if manifest is None:
        return text
    secrets = set()
    for key, item in manifest.config.items():
        if item.type != "secret":
            continue
        value = values.get(key, item.default)
        if isinstance(value, str) and value:
            secrets.update((value, encode(value)[1:-1], repr(value)[1:-1], quote(value, safe=""), quote_plus(value)))
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return text


def read_manifest(directory: Path) -> Manifest:
    path = directory / "plugin.toml"
    text = path.read_text(encoding="utf-8")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}; 原文开头：{text[:300]!r}") from error
    interface = raw.get("interface")
    if interface != INTERFACE:
        raise ValueError(f"{path}: 插件接口版本 {interface!r} 与宿主接口版本 {INTERFACE} 不一致，未加载（不做兼容）")
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"{path}: {error}") from error
    if manifest.name != directory.name:
        raise ValueError(f"{path}: name {manifest.name!r} 必须等于目录名 {directory.name!r}")
    return manifest


def discover(paths: list[Path]) -> tuple[dict[str, list[Path]], list[str]]:
    """Plugin directories by name across the builtin directory and ``plugins.paths``."""
    found: dict[str, list[Path]] = {}
    errors = []
    for base in (BUILTIN, *paths):
        if not base.is_dir():
            errors.append(f"插件目录不存在或不是目录：{base}")
            continue
        for directory in sorted(base.iterdir()):
            if (directory / "plugin.toml").is_file():
                found.setdefault(directory.name, []).append(directory)
    return found, errors


def scene_skill_catalog(config: HostConfig, scene: str) -> tuple[Skill, ...]:
    """Read a saved scene's skill sources without importing plugin executable code."""
    if config.worker is None:
        return ()
    skills = list(load_catalog(config.worker.skills_directory, scene,
                               public_browser=config.worker.public_browser))
    if config.plugins is not None:
        found, _ = discover(config.plugins.paths)
        for name in config.scenes[scene].plugins:
            directories = found.get(name, [])
            if len(directories) != 1:
                raise ValueError(f"插件 {name} 的技能来源无法定位到唯一目录：{directories}")
            read_manifest(directories[0])
            skills.extend(load_plugin_skills(directories[0] / "skills", name))
    return tuple(skills)




@dataclass
class Background:
    method: str
    seconds: int
    last_started: float | None = None
    last_finished: float | None = None
    last_error: str | None = None


@dataclass
class CronJob:
    name: str
    scene: str
    cron: Cron
    timezone: str
    next_run: float
    last_started: float | None = None
    last_finished: float | None = None
    last_error: str | None = None


@dataclass
class Loaded:
    name: str
    directory: Path | None
    manifest: Manifest | None
    scenes: tuple[str, ...]
    status: Literal["loaded", "running", "failed", "stopped"] = "failed"
    error: str | None = None
    instance: Plugin | None = None
    context: PluginContext | None = None
    commands: dict[str, tuple[str, str]] = field(default_factory=dict)     # name -> (description, method)
    fullmatches: dict[str, tuple[str, str]] = field(default_factory=dict)
    patterns: list[Pattern] = field(default_factory=list)
    notices: dict[str, str] = field(default_factory=dict)                   # type -> method
    tools: dict[str, tuple[str, type[BaseModel], str]] = field(default_factory=dict)
    backgrounds: list[Background] = field(default_factory=list)
    crons: dict[tuple[str, str], CronJob] = field(default_factory=dict)
    skills: tuple[Skill, ...] = ()
    errors: deque = field(default_factory=lambda: deque(maxlen=ERROR_LIMIT))
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(frozen=True)
class Pattern:
    expression: re.Pattern[str]
    description: str
    method: str
    priority: int


@dataclass(frozen=True)
class Matched:
    record: Loaded
    label: str
    method: str
    arguments: tuple = ()


def _arguments(tool: str, function: Callable) -> type[BaseModel]:
    hints = get_type_hints(function, include_extras=True)
    fields = {}
    for parameter in list(inspect.signature(function).parameters.values())[2:]:
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            raise ValueError(f"工具 {tool} 的参数 {parameter.name} 必须是普通命名参数")
        if parameter.name not in hints:
            raise ValueError(f"工具 {tool} 的参数 {parameter.name} 缺少类型标注")
        default = ... if parameter.default is parameter.empty else parameter.default
        fields[parameter.name] = (hints[parameter.name], default)
    return create_model(f"PluginTool_{tool}", __config__=STRICT, **fields)


class PluginHost:
    """Loaded plugins of one multi-scene host; ``bind`` attaches the running scenes."""

    def __init__(self, config: HostConfig, *, core_tools: set[str], now: Callable[[], float] = time.time):
        self.config = config
        self.now = now
        self.runtime: NetworkRuntime | None = None
        self.on_update: Callable[[], None] | None = None
        settings = config.plugins
        self.plugins: dict[str, Loaded] = {}
        self.available: dict[str, list[dict]] = {}
        self.discovery_errors: list[str] = []
        self.commands: dict[str, str] = {}
        self.fullmatches: dict[str, str] = {}
        self.patterns: dict[str, str] = {}
        self.tool_owner: dict[str, str] = {}
        self.tasks: dict[asyncio.Task, str] = {}
        self.closing = False
        found, self.discovery_errors = discover([] if settings is None else settings.paths)
        for name, directories in found.items():
            entries = []
            for directory in directories:
                try:
                    entries.append({"directory": str(directory), "manifest": read_manifest(directory), "error": None})
                except (OSError, ValueError) as error:
                    entries.append({"directory": str(directory), "manifest": None,
                                    "error": f"{type(error).__name__}: {error}"})
            self.available[name] = entries
        if settings is None:
            return
        for name, values in settings.configured.items():
            scenes = tuple(scene for scene, item in config.scenes.items() if name in item.plugins)
            record = Loaded(name, None, None, scenes)
            self.plugins[name] = record
            try:
                self._load(record, found.get(name, []), values, settings.data_directory / name, core_tools)
            except Exception as error:
                record.status, record.error = "failed", self.redact(name, f"{type(error).__name__}: {error}")
                self._remove_entries(record)
                logger.error("插件 %s 加载失败：%s", name, record.error)

    def _load(self, record: Loaded, directories: list[Path], values: dict, data_dir: Path,
              core_tools: set[str]) -> None:
        if len(directories) != 1:
            raise ValueError("未在内置目录和 plugins.paths 中找到" if not directories else
                             "多个目录提供同名插件：" + "、".join(map(str, directories)))
        record.directory = directory = directories[0]
        record.manifest = manifest = read_manifest(directory)
        record.skills = load_plugin_skills(directory / "skills", record.name)
        try:
            parsed = manifest.values_model().model_validate(values)
        except ValidationError as error:
            raise ValueError(f"plugins.{record.name} 配置不合法：{error}") from error
        module_name = f"lenbot_plugin_{record.name}"
        spec = importlib.util.spec_from_file_location(module_name, directory / "__init__.py",
                                                      submodule_search_locations=[str(directory)])
        if spec is None or spec.loader is None:
            raise ValueError(f"{directory} 缺少可导入的 __init__.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[module_name]
            raise
        classes = [value for value in vars(module).values()
                   if isinstance(value, type) and issubclass(value, Plugin) and value is not Plugin
                   and (value.__module__ == module_name or value.__module__.startswith(module_name + "."))]
        if len(classes) != 1:
            raise ValueError(f"插件模块必须恰好定义一个 Plugin 子类，实际 {len(classes)} 个")
        cls = classes[0]
        for attribute in dir(cls):
            mark = getattr(getattr(cls, attribute), MARK, None)
            if mark is None:
                continue
            if not inspect.iscoroutinefunction(getattr(cls, attribute)):
                raise ValueError(f"{attribute} 必须是 async 函数")
            if mark[0] == "command":
                if mark[1] in self.commands or mark[1] in record.commands:
                    raise ValueError(f"命令 /{mark[1]} 已由插件 {self.commands.get(mark[1], record.name)} 注册")
                record.commands[mark[1]] = (mark[2], attribute)
            elif mark[0] == "fullmatch":
                if mark[1] in self.fullmatches or mark[1] in record.fullmatches:
                    raise ValueError(f"全文规则 {mark[1]!r} 已注册")
                record.fullmatches[mark[1]] = (mark[2], attribute)
            elif mark[0] == "regex":
                if mark[1].pattern in self.patterns or any(
                        item.expression.pattern == mark[1].pattern for item in record.patterns):
                    raise ValueError(f"正则规则 {mark[1].pattern!r} 已注册")
                record.patterns.append(Pattern(mark[1], mark[2], attribute, mark[3]))
            elif mark[0] == "notice":
                if mark[1] in record.notices:
                    raise ValueError(f"通知 {mark[1]} 在本插件重复登记")
                record.notices[mark[1]] = attribute
            elif mark[0] == "tool":
                if mark[1] in core_tools or mark[1] in self.tool_owner or mark[1] in record.tools:
                    raise ValueError(f"工具名 {mark[1]} 与核心工具或其他插件工具重复")
                record.tools[mark[1]] = (mark[2], _arguments(mark[1], getattr(cls, attribute)), attribute)
            else:
                record.backgrounds.append(Background(attribute, mark[1]))
        data_dir.mkdir(parents=True, exist_ok=True)
        record.context = PluginContext(name=record.name, config=parsed.model_dump(), data_dir=data_dir,
                                       scenes=record.scenes, host=self)
        record.instance = cls(record.context)
        record.status = "loaded"
        self.commands.update(dict.fromkeys(record.commands, record.name))
        self.fullmatches.update(dict.fromkeys(record.fullmatches, record.name))
        self.patterns.update(dict.fromkeys((item.expression.pattern for item in record.patterns), record.name))
        self.tool_owner.update(dict.fromkeys(record.tools, record.name))

    def _remove_entries(self, record: Loaded) -> None:
        for name in record.commands:
            if self.commands.get(name) == record.name:
                del self.commands[name]
        for text in record.fullmatches:
            if self.fullmatches.get(text) == record.name:
                del self.fullmatches[text]
        for item in record.patterns:
            if self.patterns.get(item.expression.pattern) == record.name:
                del self.patterns[item.expression.pattern]
        for name in record.tools:
            if self.tool_owner.get(name) == record.name:
                del self.tool_owner[name]

    # Host capabilities used through PluginContext.

    @property
    def bot_qq(self) -> str:
        return self.config.bot_qq

    def start_task(self, plugin: str, name: str, coroutine: Coroutine) -> asyncio.Task:
        if self.closing or self.plugins[plugin].status not in {'loaded','running'}:
            coroutine.close()
            raise RuntimeError("插件未处于可运行状态，不创建后台任务")
        return self._spawn(self.plugins[plugin], name, coroutine)

    def report_error(self, plugin: str, where: str, error: Exception) -> str:
        return self._record(self.plugins[plugin], where, error)

    def require_owner(self, scene: str, requester_qq: str) -> None:
        if scene not in self.config.scenes:
            raise PermissionError(f"未配置场景 {scene}")
        if requester_qq in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError(f'QQ {requester_qq} 在当前场景黑名单中')
        if self.config.owner_qq is None or requester_qq != self.config.owner_qq:
            raise PermissionError(f"QQ {requester_qq} 没有主人账号权限（按根配置 owner_qq 判断）")

    def redact(self, plugin: str, text: str) -> str:
        record = self.plugins[plugin]
        return redact_values(text, record.manifest, self.config.plugins.configured[plugin])

    def scene_timezone(self, scene: str) -> str:
        return self.config.scene_timezone(scene)

    def _active(self, plugin: str, scene: str | None = None) -> Loaded:
        record = self.plugins[plugin]
        if scene is not None and scene not in record.scenes:
            raise PermissionError(f"插件 {plugin} 未在场景 {scene} 启用")
        if self.closing or record.status not in {"loaded", "running"}:
            raise RuntimeError(f"插件 {plugin} 未处于可运行状态：{record.status}")
        return record

    async def get_kv(self, plugin: str, key: str, default: JsonValue) -> JsonValue:
        return PluginKV(self._active(plugin).context.data_dir).get(key, default)

    async def set_kv(self, plugin: str, key: str, value: JsonValue) -> None:
        PluginKV(self._active(plugin).context.data_dir).set(key, value)

    async def delete_kv(self, plugin: str, key: str) -> bool:
        return PluginKV(self._active(plugin).context.data_dir).delete(key)

    async def send_text(self, plugin: str, scene: str, text: str, reply_to: str | None) -> Sent:
        return await self.send_parts(plugin, scene, [Text(text)], reply_to)

    async def send_parts(self, plugin: str, scene: str, parts: Sequence[Content], reply_to: str | None) -> Sent:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        safe_parts = [Text(self.redact(plugin, part.text)) if isinstance(part, Text)
                      else Image(part.data, self.redact(plugin, part.description)) if isinstance(part, Image)
                      else part for part in parts]
        return await self.runtime.chats[scene].send_plugin_content(plugin, safe_parts, reply_to=reply_to)

    def emit_event(self, plugin: str, scene: str, text: str) -> None:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        now = datetime.fromtimestamp(self.now(), ZoneInfo(self.scene_timezone(scene)))
        content = Template((PROMPTS / "next_plugin_event.md").read_text(encoding="utf-8")).substitute(
            plugin=plugin, time=now.isoformat(timespec="seconds"), text=self.redact(plugin, text.strip())).strip()
        self.runtime.store.add_plugin_event(scene, plugin, "event", content)
        self.runtime.runners[scene].changed.set()
        self._notify()

    def recent_messages(self, plugin: str, scene: str, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        return self.runtime.store.recent(scene, limit)

    async def memory(self, plugin: str, scene: str, arguments: dict) -> str:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        chat = self.runtime.chats[scene]
        if chat.memory is None:
            raise ValueError(f"场景 {scene} 未配置长期记忆服务")
        if "memory" not in chat.allowed_tool_names:
            raise PermissionError(f"场景 {scene} 当前加载角色未允许 memory")
        return await chat.memory.execute(scene, arguments)

    async def generate(self, plugin: str, scene: str, prompt: str, role: GenerationRole,
                       system: str | None) -> str:
        self._active(plugin, scene)
        messages = ([] if system is None else [{"role": "system", "content": system}])
        messages.append({"role": "user", "content": prompt})

        def text_reply(reply: ModelReply) -> None:
            if reply.tool_calls or not reply.text.strip():
                raise ValueError(f"插件单次生成未返回完整文字：{encode(reply.message)}")

        async with ChatModel(self.config.model_settings(role)) as model:
            reply = await request_model(
                self.config, self.runtime.store, model, messages, [], scene=scene, role=role,
                plugin=plugin, slots=self.runtime.slots, validate=text_reply, notify=self._notify)
        return reply.text

    async def delegate(self, plugin: str, scene: str, requester: str, goal: str, deliverable: str,
                       context: str, materials: Sequence[str]) -> dict:
        self._active(plugin, scene)
        if self.runtime.tasks is None:
            raise ValueError("当前宿主未启用独立工作任务")
        from .tasks_tools import DelegateArguments
        arguments = DelegateArguments(goal=goal, deliverable=deliverable, requester=requester,
                                      context=context, materials=list(materials))
        return await self.runtime.tasks.delegate(scene, **arguments.model_dump())

    def cron(self, plugin: str, name: str, scene: str, expression: str, timezone: str,
             handler: Callable[[Invocation], Awaitable[None]]) -> asyncio.Task:
        record = self._active(plugin, scene)
        key = (scene, name)
        if key in record.crons:
            raise ValueError(f"插件 {plugin} 在 {scene} 已登记定点任务 {name}")
        cron = parse_cron("cron:" + expression)
        job = CronJob(name, scene, cron, timezone, next_cron(cron, timezone, self.now()))
        record.crons[key] = job
        return self.start_task(plugin, f"定点 {scene}/{name}", self._cron(record, job, handler))

    async def _cron(self, record: Loaded, job: CronJob,
                    handler: Callable[[Invocation], Awaitable[None]]) -> None:
        await record.ready.wait()
        while True:
            await asyncio.sleep(max(0, job.next_run - self.now()))
            job.last_started = self.now()
            try:
                await handler(Invocation(record.context, job.scene))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                job.last_error = self._record(record, f"定点 {job.scene}/{job.name}", error)
            else:
                job.last_error = None
            job.last_finished = self.now()
            job.next_run = next_cron(job.cron, job.timezone, self.now())
            self._notify()

    def skills_for(self, scene: str) -> tuple[Skill, ...]:
        return tuple(skill for record in self.plugins.values()
                     if record.status in {"loaded", "running"} and scene in record.scenes for skill in record.skills)

    # Runtime entry points.

    def bind(self, runtime: NetworkRuntime) -> None:
        self.runtime = runtime

    def _notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    def _record(self, record: Loaded, where: str, error: BaseException) -> str:
        text = self.redact(record.name, f"{type(error).__name__}: {error}")
        where = self.redact(record.name, where)
        record.errors.append({"at": self.now(), "where": where, "error": text})
        logger.error("插件 %s %s 出错：%s", record.name, where, text)
        self._notify()
        return text

    async def _guard(self, record: Loaded, where: str, call: Awaitable[object]) -> None:
        try:
            await call
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._record(record, where, error)

    def _spawn(self, record: Loaded, where: str, call: Coroutine) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(self._guard(record, where, call), name=f"{record.name}:{where}")
        self.tasks[task] = record.name
        def finished(task):
            self.tasks.pop(task, None)
            call.close()
        task.add_done_callback(finished)
        return task

    def tools_for(self, scene: str, *, preparing: bool = False) -> list[ExternalTool]:
        tools = []
        for record in self.plugins.values():
            if (record.status != "running" and not (preparing and record.status == "loaded")) or scene not in record.scenes:
                continue
            for name, (description, model, method) in record.tools.items():
                tools.append(ExternalTool(name=name, description=description,
                                          parameters=model.model_json_schema(), source=f"插件 {record.name}",
                                          call=self._tool_call(record, name, model, method)))
        return tools

    def _tool_call(self, record: Loaded, name: str, model: type[BaseModel], method: str
                   ) -> Callable[[str, dict], Awaitable[str]]:
        async def call(scene: str, arguments: dict) -> str:
            try:
                self._active(record.name, scene)
                if record.status != "running":
                    raise RuntimeError(f"插件 {record.name} 未运行：{record.status}；{record.error or '尚未启动或已经停止'}")
                parsed = model.model_validate(arguments)
                result = await getattr(record.instance, method)(
                    Invocation(record.context, scene), **{key: getattr(parsed, key) for key in model.model_fields})
                if not isinstance(result, str):
                    raise TypeError(f"插件工具必须返回文本，实际 {type(result).__name__}")
            except Exception as error:
                safe = self._record(record, f"工具 {name}", error)
                if safe != f"{type(error).__name__}: {error}":
                    raise RuntimeError(safe) from None
                raise
            return self.redact(record.name, result)
        return call

    def match_message(self, message: ChatMessage, other_bots: tuple[str, ...]) -> Matched | None:
        if (self.closing or message.is_self or message.sender.uid in other_bots
                or message.sender.uid in self.config.scene_config(message.scene).permissions.blacklist):
            return None
        # Only transport prefixes may precede text. Media/other mentions are not invisible glue.
        pieces = []
        started = False
        for segment in message.segments:
            if segment.type == "text":
                pieces.append(segment.data["text"])
                started = started or bool(segment.data["text"].strip())
            elif not started and (segment.type == "reply" or
                                  (segment.type == "at" and str(segment.data["qq"]) == self.bot_qq)):
                continue
            else:
                return None
        text = "".join(pieces).strip()
        eligible = {name: item for name, item in self.plugins.items()
                    if item.status in {"loaded", "running"} and message.scene in item.scenes}
        match = re.fullmatch(r"/(\S+)(?:\s+(.*))?", text, re.DOTALL)
        if match is not None and (record := eligible.get(self.commands.get(match[1]))) is not None:
            return Matched(record, f"命令 /{match[1]}", record.commands[match[1]][1], ((match[2] or "").strip(),))
        if (record := eligible.get(self.fullmatches.get(text))) is not None:
            return Matched(record, f"全文 {text!r}", record.fullmatches[text][1])
        patterns = sorted(((record, item) for record in eligible.values() for item in record.patterns),
                          key=lambda pair: (-pair[1].priority, pair[0].name, pair[1].method))
        for record, item in patterns:
            if (match := item.expression.fullmatch(text)) is not None:
                return Matched(record, f"正则 {item.expression.pattern!r}", item.method, (match,))
        return None

    def message_report(self, message: ChatMessage, matched: Matched, result: str) -> str:
        now = datetime.fromtimestamp(self.now(), ZoneInfo(self.scene_timezone(message.scene)))
        return Template((PROMPTS / "next_plugin_handled.md").read_text(encoding="utf-8")).substitute(
            plugin=matched.record.name, time=now.isoformat(timespec="seconds"),
            message_id=message.platform_message_id, qq=message.sender.uid,
            rule=self.redact(matched.record.name, matched.label),
            result=self.redact(matched.record.name, result)).strip()

    def dispatch_message(self, message: ChatMessage, matched: Matched) -> None:
        record = matched.record

        async def invoke() -> None:
            result = "处理被中断；未确认完成。"
            try:
                await record.ready.wait()
                self._active(record.name, message.scene)
                response = await getattr(record.instance, matched.method)(
                    Invocation(record.context, message.scene, message), *matched.arguments)
                if response is not None and not isinstance(response, str):
                    raise TypeError(f"插件消息处理器须返回说明文本或 None，实际 {type(response).__name__}")
                result = "处理结束。" + ("未提供额外结果说明。" if response is None else response)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                result = "处理失败：" + self._record(record, matched.label, error)
            finally:
                self.runtime.store.add_plugin_event(
                    message.scene, record.name, "reply", self.message_report(message, matched, result))
                self._notify()

        self._spawn(record, matched.label, invoke())

    def _dispatch(self, record: Loaded, where: str, method: Callable, *arguments: object) -> None:
        async def invoke() -> None:
            await record.ready.wait()
            if record.status != "running":
                raise RuntimeError(f"插件 {record.name} 未运行：{record.status}；{record.error}")
            await method(*arguments)
        self._spawn(record, where, invoke())

    def handle_notice(self, notice: Notice) -> int:
        keys = {notice.notice_type} | ({f"{notice.notice_type}.{notice.sub_type}"} if notice.sub_type else set())
        count = 0
        for record in self.plugins.values():
            if record.status not in {"loaded", "running"} or notice.scene not in record.scenes:
                continue
            for key in sorted(keys & set(record.notices)):
                method = getattr(record.instance, record.notices[key])
                self._dispatch(record, f"通知 {key}", method, Invocation(record.context, notice.scene), notice)
                count += 1
        return count

    async def _background(self, record: Loaded, item: Background) -> None:
        method = getattr(record.instance, item.method)
        while True:
            item.last_started = self.now()
            try:
                await method(record.context)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                item.last_error = self._record(record, f"后台 {item.method}", error)
            else:
                item.last_error = None
            item.last_finished = self.now()
            self._notify()
            await asyncio.sleep(item.seconds)

    async def start(self) -> None:
        for record in self.plugins.values():
            if record.status != "loaded":
                continue
            try:
                await record.instance.start()
            except Exception as error:
                record.status, record.error = "failed", self._record(record, "启动", error)
                self._remove_entries(record)
                owned = [task for task, owner in self.tasks.items() if owner == record.name]
                for task in owned:
                    task.cancel()
                await asyncio.gather(*owned, return_exceptions=True)
                try:
                    await record.instance.stop()
                except Exception as cleanup:
                    self._record(record, "启动失败后清理", cleanup)
                record.ready.set()
                continue
            record.status = "running"
            record.ready.set()
            for item in record.backgrounds:
                task = asyncio.get_running_loop().create_task(self._background(record, item))
                self.tasks[task] = record.name
                task.add_done_callback(lambda task: self.tasks.pop(task, None))
        self._notify()

    async def close(self) -> None:
        self.closing = True
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for record in self.plugins.values():
            if record.status in {"loaded", "running"}:
                try:
                    await record.instance.stop()
                except Exception as error:
                    record.status, record.error = "failed", self._record(record, "停止", error)
                else:
                    record.status = "stopped"
        self._notify()

    def state(self) -> dict:
        plugins = []
        for record in self.plugins.values():
            plugins.append({
                "name": record.name, "status": record.status, "error": record.error,
                "directory": None if record.directory is None else str(record.directory),
                "version": None if record.manifest is None else record.manifest.version,
                "description": None if record.manifest is None else record.manifest.description,
                "scenes": list(record.scenes),
                "commands": [{"name": name, "description": description}
                             for name, (description, _) in record.commands.items()],
                "rules": ([{"kind": "fullmatch", "pattern": text, "description": description}
                           for text, (description, _) in record.fullmatches.items()]
                          + [{"kind": "regex", "pattern": item.expression.pattern,
                              "description": item.description, "priority": item.priority}
                             for item in record.patterns]),
                "notices": sorted(record.notices),
                "tools": [{"name": name, "description": description}
                          for name, (description, _, _) in record.tools.items()],
                "backgrounds": [{"method": item.method, "every_seconds": item.seconds,
                                 "last_started": item.last_started, "last_finished": item.last_finished,
                                 "last_error": item.last_error} for item in record.backgrounds],
                "errors": list(reversed(record.errors)),
                "crons": [{"name": job.name, "scene": job.scene, "expression": job.cron.expression,
                           "timezone": job.timezone, "next_run": job.next_run,
                           "last_started": job.last_started, "last_finished": job.last_finished,
                           "last_error": job.last_error} for job in record.crons.values()],
                "skills": [skill.name for skill in record.skills],
            })
        return {"plugins": plugins, "discovery_errors": self.discovery_errors}
