"""Load configured plugin packages and run them at the plugin error boundary (M14)."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
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
from typing import TYPE_CHECKING, Literal, get_type_hints
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model, field_validator

from .config import PLUGIN_NAME, PLUGIN_RESERVED, HostConfig
from .external_tools import ExternalTool
from .messages import ChatMessage
from .plugin import INTERFACE, MARK, Invocation, Notice, Plugin, PluginContext, Sent
from .store import encode

if TYPE_CHECKING:
    from .network import NetworkRuntime


logger = logging.getLogger(__name__)
BUILTIN = Path(__file__).with_name("builtin_plugins")
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
STRICT = ConfigDict(extra="forbid", strict=True)
FIELD_TYPES = {"string": str, "secret": str, "integer": int, "number": float, "boolean": bool,
               "string_list": list[str]}
ERROR_LIMIT = 20


class ConfigField(BaseModel):
    model_config = STRICT
    type: Literal["string", "secret", "integer", "number", "boolean", "string_list"]
    description: str = Field(min_length=1)
    default: str | int | float | bool | list[str] | None = None


class Manifest(BaseModel):
    model_config = STRICT
    name: str
    version: str = Field(min_length=1)
    interface: int
    authors: list[str] = Field(min_length=1)
    license: str = Field(min_length=1)
    description: str = Field(min_length=1)
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
        for key, item in value.items():
            if re.fullmatch(r"[a-z][a-z0-9_]*", key) is None:
                raise ValueError(f"config.{key}: keys use lowercase letters, digits and underscores")
            if "default" in item.model_fields_set:
                TypeAdapter(FIELD_TYPES[item.type], config=STRICT).validate_python(item.default)
        return value

    def values_model(self) -> type[BaseModel]:
        fields = {}
        for key, item in self.config.items():
            default = item.default if "default" in item.model_fields_set else ...
            fields[key] = (FIELD_TYPES[item.type], Field(default, description=item.description))
        return create_model(f"PluginConfig_{self.name}", __config__=STRICT, **fields)


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


def parse_notice(raw: dict) -> Notice | None:
    """Parse the fields every routed notice needs; ``None`` when it names no scene."""
    kind, sub_type = raw.get("notice_type"), raw.get("sub_type")
    if not isinstance(kind, str) or not kind or (sub_type is not None and not isinstance(sub_type, str)):
        raise ValueError(f"OneBot notice lacks a text notice_type/sub_type: {encode(raw)[:300]}")
    moment = raw.get("time")
    if isinstance(moment, bool) or not isinstance(moment, int | float):
        raise ValueError(f"OneBot notice lacks numeric time: {encode(raw)[:300]}")
    ids = {}
    # Implementations report operator_id 0 when there is no separate operator; keep it as sent.
    for name, pattern in (("group_id", r"[1-9][0-9]*"), ("user_id", r"[1-9][0-9]*"), ("operator_id", r"[0-9]+")):
        value = raw.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | str)
                                  or re.fullmatch(pattern, str(value)) is None):
            raise ValueError(f"OneBot notice {name} is not a QQ number: {encode(raw)[:300]}")
        ids[name] = None if value is None else str(value)
    scene = (f"group:{ids['group_id']}" if ids["group_id"] is not None
             else f"private:{ids['user_id']}" if ids["user_id"] is not None else None)
    if scene is None:
        return None
    return Notice(scene=scene, notice_type=kind, sub_type=sub_type, user_id=ids["user_id"],
                  operator_id=ids["operator_id"], time=float(moment), raw=raw)


@dataclass
class Background:
    method: str
    seconds: int
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
    notices: dict[str, str] = field(default_factory=dict)                   # type -> method
    tools: dict[str, tuple[str, type[BaseModel], str]] = field(default_factory=dict)
    backgrounds: list[Background] = field(default_factory=list)
    errors: deque = field(default_factory=lambda: deque(maxlen=ERROR_LIMIT))
    ready: asyncio.Event = field(default_factory=asyncio.Event)


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
        self.tool_owner: dict[str, str] = {}
        self.tasks: set[asyncio.Task] = set()
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
                record.status, record.error = "failed", f"{type(error).__name__}: {error}"
                self._remove_entries(record)
                logger.error("插件 %s 加载失败：%s", name, record.error)

    def _load(self, record: Loaded, directories: list[Path], values: dict, data_dir: Path,
              core_tools: set[str]) -> None:
        if len(directories) != 1:
            raise ValueError("未在内置目录和 plugins.paths 中找到" if not directories else
                             "多个目录提供同名插件：" + "、".join(map(str, directories)))
        record.directory = directory = directories[0]
        record.manifest = manifest = read_manifest(directory)
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
        self.tool_owner.update(dict.fromkeys(record.tools, record.name))

    def _remove_entries(self, record: Loaded) -> None:
        for name in record.commands:
            if self.commands.get(name) == record.name:
                del self.commands[name]
        for name in record.tools:
            if self.tool_owner.get(name) == record.name:
                del self.tool_owner[name]

    # Host capabilities used through PluginContext.

    def scene_timezone(self, scene: str) -> str:
        return self.config.scene_timezone(scene)

    async def send_text(self, plugin: str, scene: str, text: str, reply_to: str | None) -> Sent:
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        report, status = await self.runtime.chats[scene].send_plugin_text(plugin, text, reply_to=reply_to)
        return Sent(status, report)

    def emit_event(self, plugin: str, scene: str, text: str) -> None:
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        now = datetime.fromtimestamp(self.now(), ZoneInfo(self.scene_timezone(scene)))
        content = Template((PROMPTS / "next_plugin_event.md").read_text(encoding="utf-8")).substitute(
            plugin=plugin, time=now.isoformat(timespec="seconds"), text=text.strip()).strip()
        self.runtime.store.add_plugin_event(scene, plugin, "event", content)
        self.runtime.runners[scene].changed.set()
        self._notify()

    def recent_messages(self, scene: str, limit: int) -> list[ChatMessage]:
        if self.runtime is None:
            raise RuntimeError("插件宿主尚未接入运行中的场景")
        return self.runtime.store.recent(scene, limit)

    # Runtime entry points.

    def bind(self, runtime: NetworkRuntime) -> None:
        self.runtime = runtime

    def _notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    def _record(self, record: Loaded, where: str, error: BaseException) -> str:
        text = f"{type(error).__name__}: {error}"
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

    def _spawn(self, record: Loaded, where: str, call: Awaitable[object]) -> None:
        task = asyncio.get_running_loop().create_task(self._guard(record, where, call))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

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
                if record.status != "running":
                    raise RuntimeError(f"插件 {record.name} 未运行：{record.status}；{record.error or '尚未启动或已经停止'}")
                parsed = model.model_validate(arguments)
                result = await getattr(record.instance, method)(
                    Invocation(record.context, scene), **{key: getattr(parsed, key) for key in model.model_fields})
                if not isinstance(result, str):
                    raise TypeError(f"插件工具必须返回文本，实际 {type(result).__name__}")
            except Exception as error:
                self._record(record, f"工具 {name}", error)
                raise
            return result
        return call

    def match_command(self, message: ChatMessage, other_bots: tuple[str, ...]) -> tuple[Loaded, str, str] | None:
        if message.is_self or message.sender.uid in other_bots:
            return None
        text = "".join(segment.data["text"] for segment in message.segments if segment.type == "text").strip()
        match = re.fullmatch(r"/(\S+)(?:\s+(.*))?", text, re.DOTALL)
        if match is None or match[1] not in self.commands:
            return None
        record = self.plugins[self.commands[match[1]]]
        if record.status not in {"loaded", "running"} or message.scene not in record.scenes:
            return None
        return record, match[1], (match[2] or "").strip()

    def dispatch_command(self, message: ChatMessage, matched: tuple[Loaded, str, str]) -> None:
        record, name, args = matched
        method = getattr(record.instance, record.commands[name][1])
        self._dispatch(record, f"命令 /{name}", method, Invocation(record.context, message.scene, message), args)

    def _dispatch(self, record: Loaded, where: str, method: Callable, *arguments: object) -> None:
        async def invoke() -> None:
            await record.ready.wait()
            if record.status != "running":
                raise RuntimeError(f"插件 {record.name} 未运行：{record.status}；{record.error}")
            await method(*arguments)
        self._spawn(record, where, invoke())

    def handle_notice(self, raw: dict) -> int:
        notice = parse_notice(raw)
        if notice is None:
            return 0
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
                record.ready.set()
                continue
            record.status = "running"
            record.ready.set()
            for item in record.backgrounds:
                task = asyncio.get_running_loop().create_task(self._background(record, item))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        self._notify()

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for record in self.plugins.values():
            if record.status == "running":
                try:
                    await record.instance.stop()
                except Exception as error:
                    self._record(record, "停止", error)
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
                "notices": sorted(record.notices),
                "tools": [{"name": name, "description": description}
                          for name, (description, _, _) in record.tools.items()],
                "backgrounds": [{"method": item.method, "every_seconds": item.seconds,
                                 "last_started": item.last_started, "last_finished": item.last_finished,
                                 "last_error": item.last_error} for item in record.backgrounds],
                "errors": list(reversed(record.errors)),
            })
        return {"plugins": plugins, "discovery_errors": self.discovery_errors}
