from typing import Any, Callable, Awaitable, Literal
from pathlib import Path
from pydantic import BaseModel
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginPermission
from len_bot.events.models import Event
from len_bot.tools.results import ToolResult

class PluginContext:
    def __init__(self, manifest: PluginManifest, runtime: Any, host: Any, *, entry, config: BaseModel):
        self.manifest = manifest
        self._runtime = runtime
        self._host = host
        self.spec = entry.spec
        self.directory = entry.directory
        self.config = config

    @property
    def time_settings(self):
        return self._runtime.config_store.current.time

    @property
    def members(self):
        return tuple(self._runtime.config_store.current.members)

    @property
    def data_directory(self) -> Path:
        return Path(self._runtime.config.db_path).resolve().parent / 'plugins' / self.spec.id

    @property
    def event_store(self):
        """Built-in data readers use the store's scene-scoped query methods."""
        return self._runtime.event_store

    def has_permission(self, perm: PluginPermission) -> bool:
        return perm in self.manifest.permissions

    async def emit_event(self, event: Event) -> None:
        """Sensory Input: emits an external sensory event into AgentRuntime."""
        if not self.has_permission(PluginPermission.EMIT_EVENT):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'emit_event' permission.")
        self._host.record_plugin_event(self.manifest.id)
        await self._runtime.receive_event(event)

    def register_tool(
        self,
        name: str,
        description: str,
        parameter_model: type[BaseModel],
        handler: Callable[[BaseModel, PluginCallContext], Awaitable[ToolResult | dict[str, Any]]],
        *,
        purpose: str,
        aliases: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        kind: Literal["read", "proposal"],
        roles: tuple[Literal["conversation", "work"], ...],
        deferred: bool = False,
        available: Callable[[PluginCallContext], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        """Tool Registration: registers an agentic tool for Cognition."""
        if not self.has_permission(PluginPermission.REGISTER_TOOL):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'register_tool' permission.")
        timeout = self.manifest.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout is None or timeout <= 0:
            raise ValueError(f"Plugin '{self.spec.id}' must provide a positive tool timeout")
        self._host.register_plugin_tool(
            plugin_id=self.manifest.id,
            name=name,
            description=description,
            parameter_model=parameter_model,
            purpose=purpose, aliases=aliases, keywords=keywords,
            handler=handler,
            timeout_seconds=timeout,
            kind=kind,
            roles=roles,
            deferred=deferred,
            available=available,
        )

class BasePlugin:
    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    async def on_load(self, context: PluginContext) -> None:
        pass

    async def on_unload(self) -> None:
        pass

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    def source_status(self) -> dict[str, Any]:
        return {}
