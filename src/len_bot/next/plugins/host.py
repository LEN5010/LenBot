"""Load configured plugin packages and run them at the plugin error boundary (M14)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import datetime
import importlib.util
import inspect
import logging
from pathlib import Path
import re
import shutil
from string import Template
import sys
import time
from typing import TYPE_CHECKING, Literal, get_type_hints
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, create_model

from ..config import HostConfig
from ..tools.external_tools import ExternalTool
from ..platform.messages import ChatMessage
from ...plugin import (MARK, Content, GenerationRole, Image, Text,
                     Invocation, Notice, Plugin, PluginContext, Sent)
from .kv import PluginKV
from .data import prepare_data
from ..models.client import ChatModel, ModelReply
from ..models.request import request_model
from ..chat.schedule_time import Cron, next_cron, parse_cron
from ..chat.request_source import SOURCE_DESCRIPTION, source_message
from ..runtime.logs import SECRETS, error_text, log_context, log_event, recorded_errors
from ..tools.skills import Skill, load_plugin_skills
from .manifest import Manifest, discover, read_manifest, redact_values, secret_values
from ..storage.store import encode
from ...image_assets import MAX_IMAGE_BYTES, inspect_image
from ..tools.http_read import fetch_public
from .store import PluginStore

if TYPE_CHECKING:
    from ..runtime.network import NetworkRuntime


logger = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
STRICT = ConfigDict(extra="forbid", strict=True)
ERROR_LIMIT = 20
JSON_RESULT = TypeAdapter(JsonValue, config=ConfigDict(strict=True, allow_inf_nan=False))


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


@dataclass(frozen=True)
class ToolEntry:
    description: str
    summary: str | None
    model: type[BaseModel]
    method: str
    needs_source: bool


@dataclass
class Loaded:
    name: str
    directory: Path | None
    manifest: Manifest | None
    scenes: tuple[str, ...]
    values: dict[str, object] = field(default_factory=dict)
    status: Literal["loaded", "running", "failed", "stopped"] = "failed"
    error: str | None = None
    instance: Plugin | None = None
    context: PluginContext | None = None
    commands: dict[str, tuple[str, str]] = field(default_factory=dict)     # name -> (description, method)
    fullmatches: dict[str, tuple[str, str]] = field(default_factory=dict)
    patterns: list[Pattern] = field(default_factory=list)
    notices: dict[str, str] = field(default_factory=dict)                   # type -> method
    tools: dict[str, ToolEntry] = field(default_factory=dict)
    backgrounds: list[Background] = field(default_factory=list)
    crons: dict[tuple[str, str], CronJob] = field(default_factory=dict)
    instructions: str | None = None
    skills: tuple[Skill, ...] = ()
    errors: deque = field(default_factory=lambda: deque(maxlen=ERROR_LIMIT))
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


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


def _arguments(tool: str, function: Callable, *, needs_source: bool) -> type[BaseModel]:
    hints = get_type_hints(function, include_extras=True)
    fields = {}
    for parameter in list(inspect.signature(function).parameters.values())[2:]:
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            raise ValueError(f"工具 {tool} 的参数 {parameter.name} 必须是普通命名参数")
        if parameter.name not in hints:
            raise ValueError(f"工具 {tool} 的参数 {parameter.name} 缺少类型标注")
        default = ... if parameter.default is parameter.empty else parameter.default
        fields[parameter.name] = (hints[parameter.name], default)
    if needs_source:
        if "source_message_id" in fields:
            raise ValueError(f"工具 {tool} 的 source_message_id 由宿主提供，不能声明在处理函数中")
        fields["source_message_id"] = (str, Field(min_length=1, description=SOURCE_DESCRIPTION))
    return create_model(f"PluginTool_{tool}", __config__=STRICT, **fields)


class PluginHost:
    """Loaded plugins of one multi-scene host; ``bind`` attaches the running scenes."""

    def __init__(self, config: HostConfig, *, core_tools: set[str], now: Callable[[], float] = time.time):
        self.config = config
        self.core_tools = core_tools
        self.started = False
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
        previous = recorded_errors(config.logging.directory, 'plugin_error', 'plugin', limit=ERROR_LIMIT)
        for name, values in settings.configured.items():
            scenes = tuple(scene for scene, item in config.scenes.items() if name in item.plugins)
            record = Loaded(name, None, None, scenes, values=values)
            record.errors.extend(previous.get(name, []))
            self.plugins[name] = record
            if name in settings.disabled:
                record.status = "stopped"
                continue
            try:
                self._load(record, found.get(name, []), values, settings.data_directory / name, core_tools)
            except Exception as error:
                record.status, record.error = "failed", self.redact(name, f"{type(error).__name__}: {error}")
                self._remove_entries(record)
                logger.error("插件 %s 加载失败：%s", name, record.error)

    def _load(self, record: Loaded, directories: list[Path], values: dict, data_dir: Path,
              core_tools: set[str]) -> None:
        if len(directories) != 1:
            raise ValueError("未在 plugins.paths 中找到" if not directories else
                             "多个目录提供同名插件：" + "、".join(map(str, directories)))
        record.directory = directory = directories[0]
        for cache in directory.rglob('__pycache__'):
            shutil.rmtree(cache)
        record.manifest = manifest = read_manifest(directory)
        SECRETS.add(secret_values(manifest, values))
        if manifest.model is not None:
            instructions = (directory / manifest.model.instructions).resolve()
            if not instructions.is_relative_to(directory.resolve()):
                raise ValueError("model.instructions 必须位于插件目录内")
            record.instructions = instructions.read_text(encoding="utf-8").strip()
        record.skills = load_plugin_skills(directory / "skills", record.name)
        try:
            parsed = manifest.values_model(self.config.scenes).model_validate(values)
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
                record.tools[mark[1]] = ToolEntry(mark[2], mark[3],
                    _arguments(mark[1], getattr(cls, attribute), needs_source=mark[4]), attribute, mark[4])
            else:
                record.backgrounds.append(Background(attribute, mark[1]))
        data_dir.mkdir(parents=True, exist_ok=True)
        record.context = PluginContext(name=record.name, config=parsed.model_dump(), data_dir=data_dir,
                                       enabled_scenes=record.scenes, host=self)
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
    def bot_id(self) -> str:
        return self.config.bot_id

    def start_task(self, plugin: str, name: str, coroutine: Coroutine) -> asyncio.Task:
        if self.closing or self.plugins[plugin].status not in {'loaded','running'}:
            coroutine.close()
            raise RuntimeError("插件未处于可运行状态，不创建后台任务")
        return self._spawn(self.plugins[plugin], name, coroutine)

    def report_error(self, plugin: str, where: str, error: Exception) -> str:
        return self._record(self.plugins[plugin], where, error)

    def require_owner(self, scene: str, requester_id: str) -> None:
        if scene not in self.config.scenes:
            raise PermissionError(f"未配置场景 {scene}")
        if requester_id in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError(f'账号 {requester_id} 在当前场景黑名单中')
        if requester_id not in self.config.owners:
            raise PermissionError(f"账号 {requester_id} 没有主人账号权限（按根配置 owners 判断）")

    def redact(self, plugin: str, text: str) -> str:
        record = self.plugins[plugin]
        return redact_values(text, record.manifest, record.values)

    def scene_timezone(self, scene: str) -> str:
        return self.config.scene_timezone(scene)

    def scene_paused(self, scene: str) -> bool:
        """Whether the operator switched this scene's chat off; plugins stay silent there too."""
        return self.runtime is not None and self.runtime.runners[scene].state.paused

    def _speaking(self, plugin: str, scene: str) -> Loaded:
        record = self._active(plugin, scene)
        if self.scene_paused(scene):
            raise PermissionError(f"场景 {scene} 已关闭聊天，插件 {plugin} 不在该群发言")
        return record

    def _active(self, plugin: str, scene: str | None = None) -> Loaded:
        record = self.plugins[plugin]
        if scene is not None and scene not in record.scenes:
            raise PermissionError(f"插件 {plugin} 未在场景 {scene} 启用")
        if self.closing or record.status not in {"loaded", "running"}:
            raise RuntimeError(f"插件 {plugin} 未处于可运行状态：{record.status}")
        return record

    # KV runs in a worker thread: a lock wait in one plugin's file must not stall the event loop.
    async def get_kv(self, plugin: str, key: str, default: JsonValue) -> JsonValue:
        return await asyncio.to_thread(PluginKV(self._active(plugin).context.data_dir).get, key, default)

    async def set_kv(self, plugin: str, key: str, value: JsonValue) -> None:
        await asyncio.to_thread(PluginKV(self._active(plugin).context.data_dir).set, key, value)

    async def delete_kv(self, plugin: str, key: str) -> bool:
        return await asyncio.to_thread(PluginKV(self._active(plugin).context.data_dir).delete, key)

    async def fetch_image(self, plugin: str, url: str, timeout_seconds: float) -> bytes:
        self._active(plugin)
        async with asyncio.timeout(timeout_seconds):
            _, _, data = await fetch_public(url, timeout_seconds, lambda _type, _prefix: MAX_IMAGE_BYTES,
                                           fake_ip_networks=self.config.network.networks(), public_dns_url=self.config.network.public_dns_url)
        await asyncio.to_thread(inspect_image, data)
        return data

    def source_message(self, scene: str, platform_id: str) -> ChatMessage:
        return source_message(self.runtime.store, self.config.scene_config(scene), platform_id)

    async def send_text(self, plugin: str, scene: str, text: str, reply_to: str | None) -> Sent:
        return await self.send_parts(plugin, scene, [Text(text)], reply_to)

    async def send_parts(self, plugin: str, scene: str, parts: Sequence[Content], reply_to: str | None) -> Sent:
        self._speaking(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        safe_parts = [Text(self.redact(plugin, part.text)) if isinstance(part, Text)
                      else Image(part.data, self.redact(plugin, part.description)) if isinstance(part, Image)
                      else part for part in parts]
        return await self.runtime.chats[scene].expression.send_plugin_content(plugin, safe_parts, reply_to=reply_to)

    def emit_event(self, plugin: str, scene: str, text: str) -> None:
        self._speaking(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        now = datetime.fromtimestamp(self.now(), ZoneInfo(self.scene_timezone(scene)))
        content = Template((PROMPTS / "next_plugin_event.md").read_text(encoding="utf-8")).substitute(
            plugin=plugin, time=now.isoformat(timespec="seconds"), text=self.redact(plugin, text.strip())).strip()
        PluginStore(self.runtime.store).add_plugin_event(scene, plugin, "event", content)
        self.runtime.runners[scene].changed.set()
        self._notify()

    def recent_messages(self, plugin: str, scene: str, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        return self.runtime.store.recent(scene, limit)

    def messages_between(self, plugin: str, scene: str, after: float, before: float,
                         offset: int, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        store = self.runtime.store
        return [message for _, message in store.search_messages(
            scene, query=None, who=None, after=after, before=before, snapshot=store.max_message_seq(scene),
            offset=offset, limit=limit)]

    async def memory(self, plugin: str, scene: str, arguments: dict) -> str:
        self._active(plugin, scene)
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        chat = self.runtime.chats[scene]
        if chat.memory is None:
            raise ValueError(f"场景 {scene} 未配置长期记忆服务")
        if "memory" not in chat.toolset.allowed_tool_names:
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
        from ..work.tools import DelegateArguments
        arguments = DelegateArguments(goal=goal, deliverable=deliverable, requester=requester,
                                      context=context, materials=list(materials))
        return await self.runtime.tasks.delegate(scene, **arguments.model_dump(exclude={'resources'}), resources=arguments.resources)

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
            if self.scene_paused(job.scene):
                job.next_run = next_cron(job.cron, job.timezone, self.now())
                continue
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
        text = redact_values(error_text(error), record.manifest, record.values)
        where = redact_values(where, record.manifest, record.values)
        record.errors.append({"at": self.now(), "where": where, "error": text})
        with log_context(plugin=record.name):
            log_event(logger, 'plugin_error', f'插件 {record.name} {where} 出错', level=logging.ERROR,
                      error=error, where=where)
        self._notify()
        return text

    async def _guard(self, record: Loaded, where: str, call: Awaitable[object]) -> None:
        with log_context(plugin=record.name):
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
        if self.scene_paused(scene):
            return tools
        for record in self.plugins.values():
            if (record.status != "running" and not (preparing and record.status == "loaded")) or scene not in record.scenes:
                continue
            unavailable = record.instance.unavailable_tools(scene)
            for name, entry in record.tools.items():
                if name not in unavailable:
                    tools.append(self._external_tool(record, name, entry))
        return tools

    def _external_tool(self, record: Loaded, name: str, entry: ToolEntry) -> ExternalTool:
        return ExternalTool(name=name, description=entry.description, summary=entry.summary,
                            instructions=record.instructions, parameters=entry.model.model_json_schema(),
                            source=f"插件 {record.name}", call=self._tool_call(record, name, entry))

    def tool_previews(self, scene: str) -> list[dict]:
        """All configured plugin tools, including reasons they cannot be discovered."""
        previews = []
        for record in self.plugins.values():
            unavailable = {} if record.instance is None else record.instance.unavailable_tools(scene)
            for name, entry in record.tools.items():
                tool = self._external_tool(record, name, entry)
                reasons = []
                if record.status != "running":
                    reasons.append(f"插件未运行：{record.status}")
                if scene not in record.scenes:
                    reasons.append("插件未在当前群启用")
                if self.scene_paused(scene):
                    reasons.append("当前群已关闭聊天")
                if name in unavailable:
                    reasons.append(unavailable[name])
                previews.append({**tool.discovery, "summary": tool.discovery["description"],
                    "description": tool.description, "parameters": tool.definition["function"]["parameters"],
                    "instructions": tool.instructions, "scenes": list(record.scenes),
                    "needs_source": entry.needs_source, "reasons": reasons})
        return previews

    def _tool_call(self, record: Loaded, name: str, entry: ToolEntry
                   ) -> Callable[[str, dict], Awaitable[str]]:
        async def call(scene: str, arguments: dict) -> str:
            try:
                self._speaking(record.name, scene)
                if record.status != "running":
                    raise RuntimeError(f"插件 {record.name} 未运行：{record.status}；{record.error or '尚未启动或已经停止'}")
                unavailable = record.instance.unavailable_tools(scene)
                if name in unavailable:
                    raise ValueError(unavailable[name])
                parsed = entry.model.model_validate(arguments)
                values = {key: getattr(parsed, key) for key in entry.model.model_fields}
                message = self.source_message(scene, values.pop("source_message_id")) if entry.needs_source else None
                with log_context(plugin=record.name):
                    task = asyncio.create_task(getattr(record.instance, entry.method)(
                        Invocation(record.context, scene, message), **values), name=f"{record.name}:tool:{name}")
                self.tasks[task] = record.name
                task.add_done_callback(lambda done: self.tasks.pop(done, None))
                try:
                    result = await task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    raise RuntimeError(f"插件 {record.name} 已停止或重载，本次工具调用中断") from None
                if not isinstance(result, str):
                    result = encode(JSON_RESULT.validate_python(result))
            except Exception as error:
                safe = self._record(record, f"工具 {name}", error)
                if safe != f"{type(error).__name__}: {error}":
                    raise RuntimeError(safe) from None
                raise
            return redact_values(result, record.manifest, record.values)
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
                                  (segment.type == "mention" and str(segment.data["user"]) == self.bot_id)):
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
            message_id=message.platform_message_id, sender_id=message.sender.uid,
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
                PluginStore(self.runtime.store).add_plugin_event(
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
            if (record.status not in {"loaded", "running"} or notice.scene not in record.scenes
                    or self.scene_paused(notice.scene)):
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

    async def start_plugin(self, record: Loaded) -> None:
        async def start() -> None:
            data_dir = record.context.data_dir
            migrated = await prepare_data(record.name, record.manifest.data_version, data_dir,
                                          data_dir.parent / '.backups', record.instance.migrate_data)
            if migrated is not None:
                log_event(logger, 'plugin_data_migrated', **migrated)
            await record.instance.start()
            if record.status != "loaded":
                return
            record.status = "running"
            record.error = None
            record.ready.set()
            for item in record.backgrounds:
                task = asyncio.create_task(self._background(record, item))
                self.tasks[task] = record.name
                task.add_done_callback(lambda done: self.tasks.pop(done, None))
            self._notify()

        with log_context(plugin=record.name):
            task = asyncio.create_task(start(), name=f"{record.name}:start")
        self.tasks[task] = record.name
        task.add_done_callback(lambda done: self.tasks.pop(done, None))
        try:
            await task
        except asyncio.CancelledError:
            if record.status != "stopped":
                await self.stop_plugin(record.name)
            if asyncio.current_task().cancelling():
                raise
            return
        except Exception as error:
            failure = self._record(record, "启动", error)
            if record.status == "stopped":
                return
            try:
                await self.stop_plugin(record.name)
            except Exception as cleanup:
                self._record(record, "启动失败后清理", cleanup)
            record.status, record.error = "failed", failure
            record.ready.set()

    async def start(self) -> None:
        self.started = True
        for record in tuple(self.plugins.values()):
            if record.status == "loaded":
                await self.start_plugin(record)
        self._notify()

    def _unload_module(self, record: Loaded) -> None:
        prefix = f"lenbot_plugin_{record.name}"
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                del sys.modules[name]
        importlib.invalidate_caches()

    async def stop_plugin(self, name: str) -> None:
        record = self.plugins[name]
        async with record.stop_lock:
            record.status = "stopped"
            self._remove_entries(record)
            record.ready.set()
            owned = [task for task, owner in self.tasks.items() if owner == name]
            for task in owned:
                task.cancel()
            await asyncio.gather(*owned, return_exceptions=True)
            for task in owned:
                self.tasks.pop(task, None)
            record.crons.clear()
            try:
                if record.instance is not None:
                    await record.instance.stop()
            except Exception as error:
                record.status, record.error = "failed", self._record(record, "停止", error)
                raise
            finally:
                record.instance = None
                self._unload_module(record)
                self._notify()

    async def reload(self, name: str, saved: HostConfig) -> None:
        if self.closing:
            raise RuntimeError("插件宿主已结束，不能在停止后重载")
        if name in self.plugins:
            await self.stop_plugin(name)
        settings = saved.plugins
        if settings is None or name not in settings.configured:
            self.plugins.pop(name, None)
            self._notify()
            return
        scenes = tuple(scene for scene in self.config.scenes
                       if scene in saved.scenes and name in saved.scenes[scene].plugins)
        record = Loaded(name, None, None, scenes, values=settings.configured[name])
        self.plugins[name] = record
        if name in settings.disabled:
            record.status = "stopped"
            return
        found, self.discovery_errors = discover(settings.paths)
        reserved = set(self.core_tools)
        if self.runtime is not None and self.runtime.mcp is not None:
            reserved.update(tool.name for scene in self.config.scenes
                            for tool in self.runtime.mcp.tools_for(scene))
        try:
            directories = found.get(name, [])
            self._load(record, directories, record.values, settings.data_directory / name, reserved)
            if self.started or (self.runtime is not None and self.runtime.accepting):
                await self.start_plugin(record)
        except Exception as error:
            record.status, record.error = "failed", self._record(record, "加载", error)
            self._remove_entries(record)
            self._unload_module(record)
        self._notify()

    async def close(self) -> None:
        self.closing = True
        results = await asyncio.gather(*(self.stop_plugin(name) for name in self.plugins), return_exceptions=True)
        self._notify()
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("插件停止失败", errors)

    def state(self) -> dict:
        plugins = []
        for record in self.plugins.values():
            plugins.append({
                "name": record.name, "status": record.status, "error": record.error,
                "directory": None if record.directory is None else str(record.directory),
                "version": record.manifest.version if record.status == "running" else None,
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
                "tools": [{"name": name, "description": entry.description,
                           "summary": entry.description if entry.summary is None else entry.summary,
                           "parameters": entry.model.model_json_schema(), "needs_source": entry.needs_source}
                          for name, entry in record.tools.items()],
                "instructions": record.instructions,
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
