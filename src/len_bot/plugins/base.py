from typing import Any, Callable, Awaitable, Literal
from pathlib import Path
from pydantic import BaseModel
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginPermission
from len_bot.events.models import Event, EventType, PluginEventPayload
from len_bot.tools.results import ToolResult

class PluginContext:
    def __init__(self, manifest: PluginManifest, runtime: Any, host: Any, *, entry, config: BaseModel):
        self.manifest = manifest
        self._runtime = runtime
        self._host = host
        self.spec = entry.spec
        self.directory = entry.directory
        self.config = config.model_copy(deep=True)

    @property
    def time_settings(self):
        value = self._runtime.config_store.current.time
        return value.model_copy(deep=True) if value else None

    @property
    def members(self):
        return tuple(member.model_copy(deep=True) for member in self._runtime.config_store.current.members)

    def now(self) -> float:
        return self._runtime.clock()

    @property
    def bot_actor_id(self) -> str:
        return self._runtime.bot_actor_id

    def scene_config(self, scene_id: str) -> BaseModel | None:
        scene = self._runtime.config_store.current.scenes.get(scene_id)
        setting = scene.plugins.get(self.spec.id) if scene else None
        return setting.parsed_config.model_copy(deep=True) if setting else None

    def scene_enabled(self, scene_id: str) -> bool:
        return self._runtime.scene_policy.plugin_allowed(scene_id, self.spec.id, 'handler')

    def scene_configs(self) -> tuple[tuple[str, BaseModel], ...]:
        return tuple((scene_id, self.scene_config(scene_id))
            for scene_id in self._runtime.config_store.current.scenes if self.scene_enabled(scene_id))

    def start_task(self, coroutine, *, name: str):
        """Enable-time work is owned and cancelled by this plugin's host."""
        return self._host.start_task(self.spec.id, coroutine, name=name)

    def register_hook(self, phase, *, id: str, handler, scope='own', priority=100):
        self._host.register_hook(self.spec.id, phase=phase, id=id, handler=handler, scope=scope, priority=priority)

    def register_handler(self, *, id: str, description: str, match, handler,
                         event_types=(EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED),
                         sources=('human',), priority=100, consume=False, require_to_me=False,
                         available=None, validate=None, allow_mention_all=None):
        self._host.register_handler(self.spec.id, id=id, description=description, match=match,
            handler=handler, event_types=event_types, sources=sources, priority=priority,
            consume=consume, require_to_me=require_to_me, available=available,
            validate=validate, allow_mention_all=allow_mention_all)

    async def invoke_tool(self, call: PluginCallContext, name: str, arguments: BaseModel | dict) -> ToolResult:
        from len_bot.runtime.plugin_interactions import invoke_tool
        return await invoke_tool(self._runtime, call, name, arguments)

    async def submit_message(self, call: PluginCallContext, segments, *, mention_all=False):
        from len_bot.runtime.plugin_interactions import submit_message
        return await submit_message(self._runtime, call, segments, mention_all=mention_all)

    async def save_image(self, call: PluginCallContext, png: bytes, description: str) -> str:
        await self._host.validate_call(call)
        asset = await self._runtime.media_service.save_generated(png, call.scene_id,
            call.source_event_id, description)
        return asset['id']

    async def run_agent(self, call: PluginCallContext, **options):
        from len_bot.runtime.plugin_interactions import run_agent
        return await run_agent(self._runtime, call, **options)

    async def stage_work(self, call: PluginCallContext, **options):
        await self._host.validate_call(call)
        if (call.ledger is None or call.scene_id!=call.ledger.context.refs.scene_id
                or call.episode_id!=call.ledger.episode_id):
            raise ValueError('Work creation requires the active scene proposal ledger')
        return await call.ledger.stage_plugin_work(call,**options)

    @property
    def data_directory(self) -> Path:
        return Path(self._runtime.config.db_path).resolve().parent / 'plugins' / self.spec.id

    @property
    def event_store(self):
        """Built-in data readers use the store's scene-scoped query methods."""
        return self._runtime.event_store

    def has_permission(self, perm: PluginPermission) -> bool:
        return perm in self.manifest.permissions

    async def emit_event(self, name: str, payload: BaseModel, *, scene_id: str, event_id: str,
                         timestamp: float) -> None:
        """Publish this plugin's declared, typed fact through the normal Actor."""
        if not self.has_permission(PluginPermission.EMIT_EVENT):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'emit_event' permission.")
        if not self.manifest.enabled or not self.scene_enabled(scene_id):
            raise ValueError('Plugin event entry is disabled in this scene')
        model = dict(self.spec.event_models).get(name)
        if model is None or not isinstance(payload, model):
            raise ValueError(f'Plugin event {name!r} needs its registered payload model')
        envelope = PluginEventPayload(plugin_id=self.spec.id, plugin_version=self.spec.version,
            name=name, data=payload.model_dump(mode='json'))
        event = Event(id=event_id, event_type=EventType.PLUGIN_EVENT, scene_id=scene_id,
            actor_id=f'plugin:{self.spec.id}', timestamp=timestamp, payload=envelope.model_dump())
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
        page_chars: int | None = None,
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
            page_chars=page_chars,
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

    async def apply_config(self, config: BaseModel) -> None:
        """Only descriptors explicitly choosing in_place use this hook."""
        raise NotImplementedError('Plugin must implement its declared in-place configuration update')

    def source_status(self) -> dict[str, Any]:
        return {}
