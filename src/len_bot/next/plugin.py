"""Plugin authoring interface (M14, interface version 1).

A plugin package defines exactly one ``Plugin`` subclass and marks its entry
points with the decorators below. The host loads it, gives it a ``PluginContext``
and calls the marked methods at the plugin's own error boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal, Protocol

from .messages import ChatMessage


INTERFACE = 1
MARK = "__lenbot_plugin__"


def command(name: str, description: str):
    """Exact command ``/name``; handler ``(self, ctx: Invocation, args: str)``."""
    if not re.fullmatch(r"[^\s/]{1,32}", name):
        raise ValueError(f"命令名必须是 1–32 个非空白字符且不含 /：{name!r}")
    return _mark(("command", name, description))


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


@dataclass(frozen=True)
class Notice:
    """One OneBot notice routed to a configured scene; ``raw`` keeps every original field."""
    scene: str
    notice_type: str
    sub_type: str | None
    user_id: str | None
    operator_id: str | None
    time: float
    raw: Mapping[str, object]


class HostPort(Protocol):
    def scene_timezone(self, scene: str) -> str: ...
    def now(self) -> float: ...
    async def send_text(self, plugin: str, scene: str, text: str, reply_to: str | None) -> Sent: ...
    def emit_event(self, plugin: str, scene: str, text: str) -> None: ...
    def recent_messages(self, scene: str, limit: int) -> list[ChatMessage]: ...


@dataclass(frozen=True)
class PluginContext:
    """Host capabilities granted to one loaded plugin."""
    name: str
    config: Mapping[str, object]
    data_dir: Path
    scenes: tuple[str, ...]
    host: HostPort = field(repr=False)

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

    async def emit_event(self, scene: str, text: str) -> None:
        if not text.strip():
            raise ValueError("场景事件文字不能为空")
        self.host.emit_event(self.name, self._scene(scene), text)

    def recent_messages(self, scene: str, limit: int = 20) -> list[ChatMessage]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        return self.host.recent_messages(self._scene(scene), limit)


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

    async def reply(self, text: str) -> Sent:
        """Send in this scene; a command reply quotes the command message."""
        reply_to = None if self.message is None else self.message.platform_message_id
        return await self.plugin.send(self.scene, text, reply_to=reply_to)

    async def emit_event(self, text: str) -> None:
        await self.plugin.emit_event(self.scene, text)

    def recent_messages(self, limit: int = 20) -> list[ChatMessage]:
        return self.plugin.recent_messages(self.scene, limit)


class Plugin:
    """Base class; subclasses mark entry points with the decorators above."""

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    async def start(self) -> None:
        """Called once after the platform connection is ready."""

    async def stop(self) -> None:
        """Called once at host shutdown after background tasks are cancelled."""


Handler = Callable[..., Awaitable[object]]
