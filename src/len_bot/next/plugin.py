"""Plugin authoring interface (M14, interface version 1).

A plugin package defines exactly one ``Plugin`` subclass and marks its entry
points with the decorators below. The host loads it, gives it a ``PluginContext``
and calls the marked methods at the plugin's own error boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal, Protocol

from pydantic import JsonValue

from .messages import ChatMessage, Notice


INTERFACE = 1
MARK = "__lenbot_plugin__"


def command(name: str, description: str):
    """Exact command ``/name``; handler ``(self, ctx: Invocation, args: str)``."""
    if not re.fullmatch(r"[^\s/]{1,32}", name):
        raise ValueError(f"命令名必须是 1–32 个非空白字符且不含 /：{name!r}")
    return _mark(("command", name, description))


def fullmatch(text: str, description: str):
    """Plain-text exact match after outer whitespace; handler ``(self, ctx)``."""
    if not text or text.strip() != text:
        raise ValueError("全文匹配须为非空且不带首尾空白的文本")
    return _mark(("fullmatch", text, description))


def regex(pattern: str, description: str, *, priority: int = 0):
    """Full regex match; handler ``(self, ctx, match: re.Match[str])``."""
    return _mark(("regex", re.compile(pattern), description, priority))


def on_notice(notice: str):
    """OneBot ``notice_type`` or ``notice_type.sub_type``; handler ``(self, ctx, notice: Notice)``."""
    if not re.fullmatch(r"[a-z_]+(\.[a-z_]+)?", notice):
        raise ValueError(f"通知类型必须是 notice_type 或 notice_type.sub_type：{notice!r}")
    return _mark(("notice", notice))


def tool(name: str, description: str):
    """Low-frequency tool; parameters come from the handler signature after ``ctx``."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
        raise ValueError(f"工具名只能使用字母、数字、下划线和连字符，最长 64：{name!r}")
    return _mark(("tool", name, description))


def background(every: str):
    """Periodic task ``(self, ctx: PluginContext)``; ``every`` is like ``30s``, ``5m`` or ``1h``."""
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h)", every)
    if match is None:
        raise ValueError(f"后台间隔必须写成正整数加 s/m/h：{every!r}")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
    if seconds < 10:
        raise ValueError("后台间隔不能少于 10 秒")
    return _mark(("background", seconds))


def _mark(value: tuple):
    def decorate(function: Callable) -> Callable:
        if hasattr(function, MARK):
            raise ValueError(f"{function.__name__} 只能标记为一种插件入口")
        setattr(function, MARK, value)
        return function
    return decorate


@dataclass(frozen=True)
class Sent:
    """Actual outcome of a plugin send; ``simulated`` never reached QQ."""
    status: Literal["sent", "failed", "unconfirmed", "simulated", "partial"]
    report: str
    message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Image:
    data: bytes = field(repr=False)
    description: str


@dataclass(frozen=True)
class Mention:
    qq: str


Content = Text | Image | Mention




class HostPort(Protocol):
    @property
    def bot_qq(self) -> str: ...
    def scene_timezone(self, scene: str) -> str: ...
    def now(self) -> float: ...
    async def send_text(self, plugin: str, scene: str, text: str, reply_to: str | None) -> Sent: ...
    async def send_parts(self, plugin: str, scene: str, parts: Sequence[Content], reply_to: str | None) -> Sent: ...
    def emit_event(self, plugin: str, scene: str, text: str) -> None: ...
    def recent_messages(self, plugin: str, scene: str, limit: int) -> list[ChatMessage]: ...
    async def get_kv(self, plugin: str, key: str, default: JsonValue) -> JsonValue: ...
    async def set_kv(self, plugin: str, key: str, value: JsonValue) -> None: ...
    async def delete_kv(self, plugin: str, key: str) -> bool: ...
    async def memory(self, plugin: str, scene: str, arguments: dict) -> str: ...
    def start_task(self, plugin: str, name: str, coroutine: Coroutine) -> asyncio.Task: ...
    def report_error(self, plugin: str, where: str, error: Exception) -> str: ...
    def require_owner(self, scene: str, requester_qq: str) -> None: ...
    def redact(self, plugin: str, text: str) -> str: ...


@dataclass(frozen=True)
class PluginContext:
    """Host capabilities granted to one loaded plugin."""
    name: str
    config: Mapping[str, object]
    data_dir: Path
    scenes: tuple[str, ...]
    host: HostPort = field(repr=False)

    @property
    def bot_qq(self) -> str:
        return self.host.bot_qq

    def start_task(self, name: str, coroutine: Coroutine) -> asyncio.Task:
        return self.host.start_task(self.name, name, coroutine)

    def report_error(self, where: str, error: Exception) -> str:
        return self.host.report_error(self.name, where, error)

    def require_owner(self, scene: str, requester_qq: str) -> None:
        self.host.require_owner(self._scene(scene), requester_qq)

    def redact(self, text: str) -> str:
        return self.host.redact(self.name, text)

    def _scene(self, scene: str) -> str:
        if scene not in self.scenes:
            raise PermissionError(f"插件 {self.name} 未在场景 {scene} 启用")
        return scene

    def timezone(self, scene: str) -> str:
        return self.host.scene_timezone(self._scene(scene))

    def now(self) -> float:
        return self.host.now()

    async def send(self, scene: str, text: str, *, reply_to: str | None = None) -> Sent:
        if not text.strip():
            raise ValueError("插件发送的文字不能为空")
        return await self.host.send_text(self.name, self._scene(scene), text, reply_to)

    async def send_parts(self, scene: str, parts: Sequence[Content], *, reply_to: str | None = None) -> Sent:
        return await self.host.send_parts(self.name, self._scene(scene), parts, reply_to)

    async def send_image(self, scene: str, data: bytes, description: str, *, reply_to: str | None = None) -> Sent:
        return await self.send_parts(scene, [Image(data, description)], reply_to=reply_to)

    async def emit_event(self, scene: str, text: str) -> None:
        if not text.strip():
            raise ValueError("场景事件文字不能为空")
        self.host.emit_event(self.name, self._scene(scene), text)

    def recent_messages(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        return self.host.recent_messages(self.name, self._scene(scene), limit)

    async def get_kv(self, key: str, default: JsonValue = None) -> JsonValue:
        """Read plugin-local business state, never runtime configuration."""
        return await self.host.get_kv(self.name, key, default)

    async def set_kv(self, key: str, value: JsonValue) -> None:
        await self.host.set_kv(self.name, key, value)

    async def delete_kv(self, key: str) -> bool:
        return await self.host.delete_kv(self.name, key)

    async def memory(self, scene: str, arguments: dict) -> str:
        """Use the scene's permitted memory service; public content remains read-only."""
        return await self.host.memory(self.name, self._scene(scene), arguments)


@dataclass(frozen=True)
class Invocation:
    """One command, notice or tool call in a scene where the plugin is enabled."""
    plugin: PluginContext
    scene: str
    message: ChatMessage | None = None

    @property
    def config(self) -> Mapping[str, object]:
        return self.plugin.config

    @property
    def data_dir(self) -> Path:
        return self.plugin.data_dir

    def timezone(self) -> str:
        return self.plugin.timezone(self.scene)

    def now(self) -> float:
        return self.plugin.now()

    async def get_kv(self, key: str, default: JsonValue = None) -> JsonValue:
        self.plugin._scene(self.scene)
        return await self.plugin.get_kv(key, default)

    async def set_kv(self, key: str, value: JsonValue) -> None:
        self.plugin._scene(self.scene)
        await self.plugin.set_kv(key, value)

    async def delete_kv(self, key: str) -> bool:
        self.plugin._scene(self.scene)
        return await self.plugin.delete_kv(key)

    async def reply(self, text: str) -> Sent:
        """Send in this scene; a command reply quotes the command message."""
        reply_to = None if self.message is None else self.message.platform_message_id
        return await self.plugin.send(self.scene, text, reply_to=reply_to)

    async def reply_parts(self, parts: Sequence[Content]) -> Sent:
        reply_to = None if self.message is None else self.message.platform_message_id
        return await self.plugin.send_parts(self.scene, parts, reply_to=reply_to)

    async def reply_image(self, data: bytes, description: str) -> Sent:
        return await self.reply_parts([Image(data, description)])

    async def emit_event(self, text: str) -> None:
        await self.plugin.emit_event(self.scene, text)

    def recent_messages(self, limit: int = 20) -> list[ChatMessage]:
        return self.plugin.recent_messages(self.scene, limit)

    async def memory(self, arguments: dict) -> str:
        return await self.plugin.memory(self.scene, arguments)


class Plugin:
    """Base class; subclasses mark entry points with the decorators above."""

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    async def start(self) -> None:
        """Called once after the platform connection is ready."""

    async def stop(self) -> None:
        """Called once at host shutdown after background tasks are cancelled."""


Handler = Callable[..., Awaitable[object]]
