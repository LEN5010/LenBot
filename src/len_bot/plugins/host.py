import asyncio
import logging
import json
import time
import uuid
from pathlib import Path
import httpx
from dataclasses import dataclass, replace
from typing import Any, Callable, Awaitable, Literal
from pydantic import BaseModel, ValidationError
from len_bot.plugins.models import ExactText, PluginCallContext, PluginHandlerDefinition, PluginManifest, PluginToolDefinition
from len_bot.events.models import EventType, PluginEventPayload, PluginOrigin
from len_bot.plugins.hooks import HOOK_VIEWS, PluginHookDefinition, PluginRunHooks
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.tools.results import ToolResult, ToolSource, error_source_url
from len_bot.tools.discovery import rank_discovery

logger = logging.getLogger(__name__)


def _registration_source(plugin_id, handler):
    target = handler if hasattr(handler, '__qualname__') else type(handler)
    return f"plugin:{plugin_id} ({target.__module__}.{target.__qualname__})"


@dataclass
class PluginRuntimeStatus:
    """Actual plugin lifecycle and execution status for the control panel."""
    state: str = "loaded"  # loaded / enabled / disabled / error
    last_error: str = ""
    error_count: int = 0
    last_event_at: float = 0.0
    last_run_at: float = 0.0


class PluginConfigurationApplyError(RuntimeError):
    """The root file was saved, but the plugin did not apply it."""


def _core_tool_sources() -> dict[str, str]:
    # Read the actual definitions at registration time, after runtime modules
    # have loaded. No independently maintained reserved-name catalog.
    from len_bot.cognition.proposals import RESPOND, TOOLS, definition
    from len_bot.runtime.job_runner import FINISH_WORK, REPORT_PROGRESS, SKILL_TOOLS, UPDATE_WORK_STATE
    from len_bot.skills.learning import MAINTENANCE_TOOLS
    from len_bot.tools.retrieval import CORE_READ_TOOLS

    groups = {
        "core:retrieval": CORE_READ_TOOLS,
        "core:proposals": [RESPOND, *(definition(name, *spec) for name, spec in TOOLS.items())],
        "core:work": [FINISH_WORK, REPORT_PROGRESS, UPDATE_WORK_STATE, *SKILL_TOOLS],
        "core:skill_maintenance": MAINTENANCE_TOOLS,
    }
    return {item['function']['name']: source for source, definitions in groups.items() for item in definitions}


class PluginHost:
    """Own plugin lifecycle and scoped native tool execution."""

    def __init__(self, runtime: Any = None):
        self.runtime = runtime
        self._plugins: dict[str, BasePlugin] = {}
        self._plugin_contexts: dict[str, PluginContext] = {}
        self._tools: dict[str, PluginToolDefinition] = {}
        self._status: dict[str, PluginRuntimeStatus] = {}
        self._handlers: dict[tuple[str, str], PluginHandlerDefinition] = {}
        self._registration_order = 0
        self._tasks: dict[str, set[asyncio.Task]] = {}
        self._task_scenes: dict[asyncio.Task,str] = {}
        self._hooks: dict[tuple[str, str], PluginHookDefinition] = {}

    def context_for(self, plugin_id):
        return self._plugin_contexts[plugin_id]

    def register_hook(self, plugin_id, **options):
        hook = PluginHookDefinition(plugin_id=plugin_id, order=self._registration_order, **options)
        if hook.phase not in HOOK_VIEWS or hook.scope not in {'own', 'conversation', 'work', 'scene'}:
            raise ValueError(f'Hook {plugin_id}/{hook.id} has an unknown phase or scope')
        key = (plugin_id, hook.id)
        if key in self._hooks:
            raise ValueError(f'Hook {plugin_id}/{hook.id} is already registered')
        self._hooks[key] = hook
        self._registration_order += 1

    def applicable_hooks(self, phase, call):
        origin = call.origin or call.entry_origin
        for hook in sorted(self._hooks.values(), key=lambda item: (item.priority, item.order)):
            plugin = self._plugins.get(hook.plugin_id)
            if (hook.phase != phase or not plugin or not plugin.manifest.enabled
                    or not self.runtime.scene_policy.plugin_allowed(call.scene_id, hook.plugin_id, call.role)):
                continue
            if (hook.scope == 'scene' or hook.scope == 'own' and origin and origin.plugin_id == hook.plugin_id
                    or hook.scope == call.role and call.entry != 'handler'):
                yield hook

    def run_hooks(self, call, audit):
        return PluginRunHooks(self, call, audit)

    def notify_delivery(self, event, cutoff):
        if event.event_type not in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}:
            return
        encoded = event.payload.get('plugin_origin')
        origin = PluginOrigin.model_validate(encoded) if encoded else None
        call = PluginCallContext(scene_id=event.scene_id, requester_qq_uid=event.payload.get('requester_qq_uid'),
            now=self.runtime.clock(), cutoff_rowid=cutoff, episode_id=event.payload.get('episode_id'),
            job_id=event.payload.get('job_id'), role='conversation', event=event,
            source_event_id=event.payload.get('origin_event_id'), origin=origin, entry_origin=origin.handler_origin or origin if origin else None,
            entry=origin.scene_entry if origin else 'chat')
        owners = dict.fromkeys(hook.plugin_id for hook in self.applicable_hooks('after_delivery', call))

        async def observe(owner):
            audit = {'receipt_event_id': event.id, 'plugin_id': owner}
            try:
                await PluginRunHooks(self, lambda: call, audit, only_plugin=owner).after_delivery(event)
            except Exception as error:
                self.record_plugin_error(owner, f'after_delivery: {error}')
            finally:
                await self.runtime.event_store.save_trace(kind='plugin_hook', scene_id=event.scene_id,
                    ref_id=f'{event.id}:{owner}', payload=audit)
        for owner in owners:
            self.start_task(owner, observe(owner), name=f'after_delivery:{event.id}',scene_id=event.scene_id)

    def start_task(self, plugin_id, coroutine, *, name, scene_id=None):
        plugin = self._plugins.get(plugin_id)
        if not plugin or not plugin.manifest.enabled:
            coroutine.close()
            raise ValueError(f'Plugin {plugin_id!r} is disabled')
        task = asyncio.create_task(coroutine, name=f'plugin:{plugin_id}:{name}')
        owned = self._tasks.setdefault(plugin_id, set())
        owned.add(task)
        if scene_id is not None:self._task_scenes[task]=scene_id
        def finished(done):
            owned.discard(done)
            self._task_scenes.pop(done,None)
            if not done.cancelled() and done.exception() is not None:
                self.record_plugin_error(plugin_id, f'{name}: {done.exception()}')
        task.add_done_callback(finished)
        return task

    async def _cancel_tasks(self, plugin_id, scene_id=None):
        tasks = tuple(task for task in self._tasks.get(plugin_id, ()) if task is not asyncio.current_task()
            and (scene_id is None or self._task_scenes.get(task)==scene_id))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def register_handler(self, plugin_id, **options):
        definition = PluginHandlerDefinition(plugin_id=plugin_id, order=self._registration_order, **options)
        key = (plugin_id, definition.id)
        if key in self._handlers:
            raise ValueError(f'Handler {key} is already registered')
        source = _registration_source(plugin_id, definition.handler)
        for other in self._handlers.values():
            if (definition.consume and other.consume and isinstance(definition.match, ExactText)
                    and isinstance(other.match, ExactText)
                    and set(definition.event_types).intersection(other.event_types)
                    and set(definition.sources).intersection(other.sources)
                    and set(definition.match.words).intersection(other.match.words)):
                raise ValueError(f'Exclusive exact command conflict: {source} / {definition.id} and '
                    f'{_registration_source(other.plugin_id, other.handler)} / {other.id}')
        if not definition.sources or set(definition.sources) - {'human', 'plugin_event', 'self_sent'}:
            raise ValueError(f'Handler {key} has invalid source kinds')
        self._handlers[key] = definition
        self._registration_order += 1

    def handler_call(self, event, route, cutoff, *, execution=None):
        origin = PluginOrigin.model_validate(route['origin'])
        return PluginCallContext(scene_id=event.scene_id,
            requester_qq_uid=event.metadata.get('requester_qq_uid'), now=self.runtime.clock(),
            cutoff_rowid=cutoff, episode_id=origin.run_id, job_id=None, role='conversation',
            source_event_id=origin.source_event_id, origin=origin, entry_origin=origin.handler_origin or origin,
            entry=origin.scene_entry,
            event=event.model_copy(deep=True), plugin=self._plugin_contexts[origin.plugin_id], execution=execution)

    def match_event(self, event, cutoff):
        """Only synchronous local matching runs under the Actor's writer."""
        from len_bot.runtime.attention import is_real_send
        if event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}:
            source_kind = 'human' if event.actor_id != self.runtime.bot_actor_id else None
        elif event.event_type == EventType.PLUGIN_EVENT:
            envelope = PluginEventPayload.model_validate(event.payload)
            entry = self.runtime.config_store.catalog.entries.get(envelope.plugin_id)
            if entry is None or event.actor_id != f'plugin:{envelope.plugin_id}':
                raise ValueError('Plugin event producer is not registered')
            model = dict(entry.spec.event_models).get(envelope.name)
            if model is None:
                raise ValueError('Plugin event payload type is not registered')
            model.model_validate(envelope.data, strict=True)
            source_kind = 'plugin_event'
        elif is_real_send(event, self.runtime.bot_actor_id):
            source_kind = 'self_sent'
        else:
            source_kind = None
        routes = []
        for definition in sorted(self._handlers.values(), key=lambda item: (item.priority, item.order)):
            plugin = self._plugins[definition.plugin_id]
            if (source_kind not in definition.sources or event.event_type not in definition.event_types
                    or not plugin.manifest.enabled
                    or not self.runtime.scene_policy.plugin_allowed(event.scene_id, definition.plugin_id, 'handler')
                    or definition.require_to_me and not (event.is_mention_bot or event.is_reply_bot
                        or event.metadata.get('quote_context', {}).get('actor_id') == self.runtime.bot_actor_id)):
                continue
            parent = event.payload.get('plugin_origin') or {}
            if source_kind == 'self_sent' and parent.get('plugin_id') == definition.plugin_id:
                continue
            origin = PluginOrigin(plugin_id=definition.plugin_id, plugin_version=plugin.manifest.version,
                entry_id=definition.id, entry_kind='handler', run_id=f'plugin:{uuid.uuid4().hex}',
                source_event_id=event.id, parent_run_id=parent.get('run_id'),scene_entry='handler')
            route = {'origin': origin.model_dump(), 'consume': definition.consume, 'state': 'matched',
                     'source_kind': source_kind, 'priority': definition.priority, 'registration_order': definition.order}
            call = self.handler_call(event, route, cutoff)
            try:
                matched = definition.match(call)
                if not isinstance(matched, bool):
                    raise TypeError('Matcher must return bool without I/O')
                if not matched:
                    continue
                if definition.available is not None and not definition.available(call):
                    continue
            except Exception as error:
                self.record_plugin_error(definition.plugin_id, f'Matcher {definition.id}: {error}')
                event.metadata.setdefault('plugin_match_errors', []).append({
                    'plugin_id': definition.plugin_id, 'handler_id': definition.id, 'error': str(error)})
                continue
            routes.append(route)
            if definition.consume:
                break
        return routes

    async def validate_call(self, call, *, mention_all=False):
        origin = call.entry_origin or call.origin
        if origin is None or not call.source_event_id or origin.source_event_id != call.source_event_id:
            raise ValueError('Plugin call needs its real source and owner')
        plugin = self._plugins.get(origin.plugin_id)
        if (not plugin or not plugin.manifest.enabled or plugin.manifest.version != origin.plugin_version
                or not self.runtime.scene_policy.plugin_allowed(call.scene_id, origin.plugin_id, call.entry)):
            raise ValueError('Plugin is disabled, unavailable in this scene, or its version changed')
        if call.origin and call.origin != origin:
            owner=self._plugins.get(call.origin.plugin_id)
            if (owner is None or not owner.manifest.enabled or owner.manifest.version != call.origin.plugin_version
                    or self._plugin_availability(call.origin.plugin_id,call) != 'callable'):
                raise ValueError('The tool owner is disabled, unavailable or changed version')
        events = await self.runtime.event_store.events_by_ids(call.scene_id, [call.source_event_id], call.cutoff_rowid)
        if len(events) != 1:
            raise ValueError('Plugin source is outside this scene or read cutoff')
        source = events[0]
        if origin.entry_kind != 'handler':
            tool=self._tools.get(origin.entry_id)
            if (tool is None or tool.plugin_id != origin.plugin_id
                    or self._plugin_availability(origin.plugin_id,call) != 'callable'):
                raise ValueError('Plugin tool source is no longer available')
            if mention_all:
                raise ValueError('Tool runs do not grant all-member mentions')
            return
        definition = self._handlers.get((origin.plugin_id, origin.entry_id))
        if definition is None or not any(route['origin'] == origin.model_dump()
                for route in source.metadata.get('plugin_routes', ())):
            raise ValueError('Plugin handler does not own this stored route')
        current = replace(call, event=source, plugin=self._plugin_contexts[origin.plugin_id])
        if definition.available and not definition.available(current):
            raise ValueError('Plugin handler entry is no longer enabled')
        if definition.validate:
            await definition.validate(current)
        if mention_all and not (definition.allow_mention_all and definition.allow_mention_all(current)):
            raise ValueError('This plugin entry has no current all-member mention setting')

    def dispatch_event(self, event, cutoff):
        from len_bot.runtime.plugin_interactions import dispatch_handler, resume_agent
        resume=event.metadata.get('conversation_resume',{}).get('state',{})
        if resume.get('plugin_origin'):
            origin=PluginOrigin.model_validate(resume['plugin_origin'])
            issue=self.origin_issue(origin,event.scene_id)
            if issue:
                self.runtime._spawn_background_task(self.runtime.event_store.save_trace(kind='plugin_run',
                    scene_id=event.scene_id,ref_id=origin.run_id,payload={'plugin_origin':origin.model_dump(),
                        'state':'interrupted','error':issue,'resume_event_id':event.id}))
                return
            self.start_task(origin.plugin_id,resume_agent(self.runtime,event,cutoff),name=f'resume:{origin.run_id}',scene_id=event.scene_id)
            return
        for route in event.metadata.get('plugin_routes', ()):
            origin = PluginOrigin.model_validate(route['origin'])
            plugin = self._plugins.get(origin.plugin_id)
            if plugin is None or not plugin.manifest.enabled:
                continue
            self.start_task(origin.plugin_id, dispatch_handler(self.runtime, event, route, cutoff),
                name=f'handler:{origin.entry_id}:{origin.run_id}',scene_id=event.scene_id)

    async def execute_handler(self, call):
        await self.validate_call(call)
        definition = self._handlers[(call.origin.plugin_id, call.origin.entry_id)]
        self.record_plugin_run(call.origin.plugin_id)
        await definition.handler(call)

    def register_plugin_tool(
        self,
        plugin_id: str,
        name: str,
        description: str,
        parameter_model: type[BaseModel],
        handler: Callable[[BaseModel, PluginCallContext], Awaitable[ToolResult | dict[str, Any]]],
        timeout_seconds: float,
        *,
        purpose: str,
        aliases: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        kind: Literal["read", "proposal"],
        roles: tuple[Literal["conversation", "work"], ...],
        deferred: bool = False,
        available: Callable[[PluginCallContext], bool] | None = None,
        page_chars: int | None = None,
    ) -> None:
        existing = self._tools.get(name)
        source = _registration_source(plugin_id, handler)
        existing_source = (_registration_source(existing.plugin_id, existing.handler)
                           if existing else _core_tool_sources().get(name))
        if existing_source:
            raise ValueError(f"Tool '{name}' registration conflict: {source} conflicts with {existing_source}.")
        self._tools[name] = PluginToolDefinition(
            plugin_id=plugin_id,
            name=name,
            description=description,
            parameter_model=parameter_model,
            purpose=purpose, aliases=aliases, keywords=keywords,
            handler=handler,
            timeout_seconds=timeout_seconds, kind=kind, roles=roles, deferred=deferred, available=available,page_chars=page_chars,
        )
        self._plugins[plugin_id].manifest.registered_tools.append(name)
        logger.info("Plugin '%s' registered tool '%s'", plugin_id, name)

    async def load_plugin(self, plugin_id: str) -> None:
        if plugin_id in self._plugins:
            raise ValueError(f'Plugin {plugin_id!r} is already loaded')
        entry = self.runtime.config_store.catalog.entries[plugin_id]
        state = self.runtime.config_store.current.plugins[plugin_id]
        if state.parsed_config is None:
            raise ValueError(f'Plugin {plugin_id!r} has no configured parameters')
        spec = entry.spec
        manifest = PluginManifest(id=spec.id, name=spec.name, version=spec.version,
            description=spec.description, plugin_type=spec.plugin_type,
            permissions=list(spec.permissions), enabled=False,
            timeout_seconds=spec.call_timeout(state.parsed_config) if spec.call_timeout else None,
            config=state.parsed_config.model_dump(), config_schema=spec.config_model.model_json_schema())
        self._status.setdefault(plugin_id, PluginRuntimeStatus()).state = 'loading'
        context = PluginContext(manifest, self.runtime, self, entry=entry, config=state.parsed_config)
        self._plugin_contexts[plugin_id] = context
        try:
            plugin = spec.create(context)
            if not isinstance(plugin, BasePlugin) or plugin.manifest is not manifest:
                raise TypeError(f'{entry.directory}: create must return a BasePlugin using context.manifest')
            self._plugins[plugin_id] = plugin
            await plugin.on_load(context)
            self._status[plugin_id].state = 'loaded'
            logger.info('Loaded plugin %s from %s', plugin_id, entry.directory)
        except Exception as error:
            self.record_plugin_error(plugin_id, f'load failed: {error}')
            await self._release_plugin(plugin_id)
            raise

    async def _release_plugin(self, plugin_id: str) -> None:
        await self._cancel_tasks(plugin_id)
        plugin = self._plugins.pop(plugin_id, None)
        if plugin is not None:
            try:
                await plugin.on_unload()
            except Exception as error:
                self.record_plugin_error(plugin_id, f'unload failed: {error}')
        self._plugin_contexts.pop(plugin_id, None)
        for name in [name for name, tool in self._tools.items() if tool.plugin_id == plugin_id]:
            del self._tools[name]
        for key in [key for key in self._handlers if key[0] == plugin_id]:
            del self._handlers[key]
        for key in [key for key in self._hooks if key[0] == plugin_id]:
            del self._hooks[key]

    async def unload_plugin(self, plugin_id: str) -> None:
        try:
            await self.disable_plugin(plugin_id)
        finally:
            await self._release_plugin(plugin_id)
        status = self._status.get(plugin_id)
        if status is not None and status.state != 'error':
            status.state = 'unloaded'

    async def unload_all(self) -> None:
        for plugin_id in list(self._plugins):
            try:
                await self.unload_plugin(plugin_id)
            except Exception as error:
                logger.error('Plugin %s shutdown failed: %s', plugin_id, error)

    async def enable_plugin(self, plugin_id: str) -> None:
        configured = self.runtime.config_store.current.plugins.get(plugin_id)
        if configured is None or not configured.enabled or configured.parsed_config is None:
            raise ValueError(f'Plugin {plugin_id!r} is not enabled in the root configuration')
        context = self._plugin_contexts.get(plugin_id)
        if context is not None and context.config != configured.parsed_config:
            await self.unload_plugin(plugin_id)
        if plugin_id not in self._plugins:
            await self.load_plugin(plugin_id)
        plugin = self._plugins[plugin_id]
        if plugin.manifest.enabled:
            return
        self._status[plugin_id].state = 'enabling'
        plugin.manifest.enabled = True
        try:
            await plugin.on_enable()
        except Exception as error:
            plugin.manifest.enabled = False
            self.record_plugin_error(plugin_id, f'enable failed: {error}')
            await self._release_plugin(plugin_id)
            raise
        self._status[plugin_id].state = 'enabled'

    async def disable_plugin(self, plugin_id: str) -> None:
        plugin = self._plugins.get(plugin_id)
        if plugin is None or self._status[plugin_id].state == 'disabled':
            return
        plugin.manifest.enabled = False
        self._status[plugin_id].state = 'disabling'
        try:
            await self.stop_scene_work(plugin_id)
            await plugin.on_disable()
        except Exception as error:
            self.record_plugin_error(plugin_id, f'disable failed: {error}')
            raise
        self._status[plugin_id].state = 'disabled'

    async def stop_scene_work(self,plugin_id,scene_id=None):
        work_ids=await self.runtime.job_runner.stop_plugin(plugin_id,scene_id)
        await self._cancel_tasks(plugin_id,scene_id)
        changed=await self.runtime.event_store.interrupt_plugin_waits_and_reminders(plugin_id,scene_id)
        if work_ids or changed['wait_ids'] or changed['reminder_ids']:
            await self.runtime.event_store.save_trace(kind='plugin_lifecycle',scene_id=scene_id or 'system:plugins',
                ref_id=plugin_id,payload={'plugin_id':plugin_id,'operation':'disable','work_ids':work_ids,**changed})

    def record_plugin_event(self, plugin_id: str) -> None:
        status = self._status.get(plugin_id)
        if status:
            status.last_event_at = time.time()

    def record_plugin_run(self, plugin_id: str) -> None:
        status = self._status.get(plugin_id)
        if status:
            status.last_run_at = time.time()

    def record_plugin_error(self, plugin_id: str, error: str) -> None:
        status = self._status.get(plugin_id)
        if status:
            status.error_count += 1
            status.last_error = error[:300]
            status.state = "error"
            logger.error("Plugin '%s' error state: %s", plugin_id, error)

    def has_plugin(self, plugin_id: str) -> bool:
        """Public membership lookup for lifecycle and configuration callers."""
        return plugin_id in self._plugins

    async def read_workspace_artifact(self, scene_id: str, job_id: str, path: str, offset: int, limit: int):
        plugin = self._plugins.get('workspace')
        if plugin is not None and hasattr(plugin, 'artifact_for_job'):
            return await plugin.artifact_for_job(scene_id, job_id, path, offset, limit)
        service = self._workspace_service_for_panel()
        return await service.read_for_job(scene_id, job_id, path, offset, limit) if service else None

    async def list_workspace_artifacts(self, scene_id: str, job_id: str):
        plugin = self._plugins.get('workspace')
        if plugin is not None and hasattr(plugin, 'artifacts_for_job'):
            return await plugin.artifacts_for_job(scene_id, job_id)
        service = self._workspace_service_for_panel()
        return await service.list_for_job(scene_id, job_id) if service else None

    async def close_job_resources(self, job: dict):
        for plugin in tuple(self._plugins.values()):
            closer = getattr(plugin, 'close_job', None)
            if closer is not None:
                try:
                    await closer(job)
                except Exception as error:
                    self.record_plugin_error(plugin.manifest.id, f'job resource cleanup: {error}')

    def _workspace_service_for_panel(self):
        setting = self.runtime.config_store.current.plugins.get('workspace')
        if not setting or setting.config is None or setting.parsed_config is None:
            return None
        from len_bot.execution.service import WorkspaceService
        from len_bot.execution.workspace import WorkspaceWorker
        context_dir = self.runtime.config.db_path
        return WorkspaceService(WorkspaceWorker(setting.parsed_config.worker,
            Path(context_dir).resolve().parent / 'plugins' / 'workspace'),
            self.runtime.event_store, 'workspace')

    def get_plugin(self, plugin_id: str) -> BasePlugin | None:
        return self._plugins.get(plugin_id)

    def get_plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Current plugin parameters from the root configuration."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        return dict(plugin.manifest.config)

    async def apply_plugin_config(self, plugin_id: str) -> None:
        setting = self.runtime.config_store.current.plugins[plugin_id]
        spec = self.runtime.config_store.catalog.entries[plugin_id].spec
        if plugin_id not in self._plugins:
            if setting.enabled:
                await self.enable_plugin(plugin_id)
            return
        if spec.config_apply == 'restart_plugin':
            await self.unload_plugin(plugin_id)
            if setting.enabled:
                await self.enable_plugin(plugin_id)
            return
        try:
            parsed = setting.parsed_config.model_copy(deep=True)
            await self._plugins[plugin_id].apply_config(parsed)
            self._plugin_contexts[plugin_id].config = parsed
            self._plugins[plugin_id].manifest.config = parsed.model_dump()
        except Exception as error:
            self.record_plugin_error(plugin_id, f'Configuration apply failed: {error}')
            await self._release_plugin(plugin_id)
            raise

    def status_snapshot(self) -> list[dict[str, Any]]:
        """Discovered metadata and observed lifecycle survive failed loads."""
        out = []
        for plugin_id, entry in self.runtime.config_store.catalog.entries.items():
            spec = entry.spec
            plugin = self._plugins.get(plugin_id)
            saved = self.runtime.config_store.current.plugins.get(plugin_id)
            configured = bool(saved and saved.config is not None)
            status = self._status.get(plugin_id, PluginRuntimeStatus(
                state='disabled' if configured else 'unconfigured'))
            out.append({
                'id': plugin_id, 'name': spec.name, 'description': spec.description,
                'version': spec.version, 'directory': str(entry.directory),
                'config_apply': spec.config_apply,
                'work': {'operation':spec.work.operation,'allowed_tools':list(spec.work.allowed_tools),
                    'parameters_schema':spec.work.parameters_model.model_json_schema(),
                    'revision_schema':spec.work.revision_model.model_json_schema()} if spec.work else None,
                'plugin_type': spec.plugin_type.value,
                'permissions': [permission.value for permission in spec.permissions],
                'enabled': bool(plugin and plugin.manifest.enabled), 'state': status.state,
                'last_error': status.last_error, 'error_count': status.error_count,
                'last_event_at': status.last_event_at, 'last_run_at': status.last_run_at,
                'config': saved.config if saved else None,
                'config_schema': spec.config_model.model_json_schema(),
                'scene_config_schema': spec.scene_config_model.model_json_schema(),
                'emitted_events': [name for name, _ in spec.event_models],
                'registered_tools': [name for name, tool in self._tools.items() if tool.plugin_id == plugin_id],
                'tools': [{'name': tool.name, 'description': tool.description, 'purpose': tool.purpose,
                           'kind': tool.kind, 'roles': list(tool.roles)} for tool in self._tools.values()
                          if tool.plugin_id == plugin_id],
                'handlers': [handler.record() for handler in self._handlers.values() if handler.plugin_id == plugin_id],
                'hooks': [hook.record() for hook in self._hooks.values() if hook.plugin_id == plugin_id],
                'active_tasks': [task.get_name() for task in self._tasks.get(plugin_id, ()) if not task.done()],
                'source_status': plugin.source_status() if plugin else {},
            })
        return out

    def _plugin_availability(self, plugin_id: str, call_context: PluginCallContext) -> str:
        plugin = self._plugins.get(plugin_id)
        configured = self.runtime.config_store.current.plugins.get(plugin_id)
        if configured is None or configured.config is None:
            return 'unconfigured'
        if not configured.enabled or plugin is None or not plugin.manifest.enabled:
            return 'plugin_disabled'
        origin = call_context.entry_origin or call_context.origin
        own_handler = call_context.entry == 'handler' and origin and origin.plugin_id == plugin_id
        requesters = call_context.requester_qq_uids or (call_context.requester_qq_uid,)
        if (not self.runtime.scene_policy.plugin_allowed(call_context.scene_id, plugin_id, call_context.role)
                or not own_handler and not any(self.runtime.scene_policy.chat_allowed(call_context.scene_id, requester) for requester in requesters)):
            return 'scene_not_enabled'
        return 'callable'

    @staticmethod
    def _tool_applies(tool: PluginToolDefinition, call_context: PluginCallContext) -> bool:
        return (call_context.role in tool.roles and (tool.kind == 'read' or call_context.ledger is not None)
                and (tool.available is None or tool.available(call_context)))

    def has_tool(self, name: str, call_context: PluginCallContext) -> bool:
        tool = self._tools.get(name)
        return bool(tool and self._plugin_availability(tool.plugin_id, call_context) == 'callable'
                    and self._tool_applies(tool, call_context))

    def capability_facts(self, call_context: PluginCallContext) -> list[dict[str, Any]]:
        """Explain current capability state without publishing hidden schemas or entry points."""
        facts = []
        for plugin_id in sorted(self.runtime.config_store.catalog.entries):
            plugin = self._plugins.get(plugin_id)
            candidates = [tool for tool in self._tools.values() if tool.plugin_id == plugin_id
                          and self._tool_applies(tool, call_context)]
            if plugin is not None and not candidates:
                continue
            status = self._plugin_availability(plugin_id, call_context)
            name = self.runtime.config_store.catalog.entries[plugin_id].spec.name
            facts.append({'plugin_id': plugin_id, 'name': name, 'status': status,
                          'purposes': sorted({tool.purpose for tool in candidates})})
        return facts

    def has_registered_tool(self, name: str) -> bool:
        """Identify the responsible boundary even after availability changed."""
        return name in self._tools

    def get_tool_names(self, call_context: PluginCallContext) -> list[str]:
        return [
            name for name, ptool in self._tools.items()
            if self.has_tool(name, call_context)
        ]

    def tool_capabilities(self, name: str) -> dict:
        tool = self._tools[name]
        return {"kind": tool.kind, "roles": tool.roles, "deferred": tool.deferred,'page_chars':tool.page_chars}

    def work_spec(self, encoded, operation):
        if encoded is None:
            if operation!='information':
                raise ValueError('Pre-upgrade specialized work has no recorded plugin owner; its original data is retained and cannot resume')
            return None
        origin=PluginOrigin.model_validate(encoded)
        entry=self.runtime.config_store.catalog.entries.get(origin.plugin_id)
        if entry is None or entry.spec.version!=origin.plugin_version:
            raise ValueError('The plugin version responsible for this work is no longer installed')
        if operation=='information':return None
        work=entry.spec.work
        if work is None or work.operation!=operation:
            raise ValueError('The original plugin work operation is no longer registered')
        return work

    def origin_issue(self,encoded,scene_id):
        origin=PluginOrigin.model_validate(encoded)
        for owner in (origin,origin.handler_origin):
            if owner is None:continue
            plugin=self._plugins.get(owner.plugin_id)
            if (plugin is None or not plugin.manifest.enabled or plugin.manifest.version!=owner.plugin_version
                    or not self.runtime.scene_policy.plugin_allowed(scene_id,owner.plugin_id,owner.scene_entry)):
                return 'The responsible plugin is disabled, unavailable in this scene, or changed version'
            if owner.entry_kind=='tool' and owner.entry_id not in self._tools:
                return 'The responsible tool is no longer registered'
            if owner.entry_kind=='handler' and (owner.plugin_id,owner.entry_id) not in self._handlers:
                return 'The responsible handler is no longer registered'
        return None

    def work_issue(self, job):
        try:
            self.work_spec(job['plugin_origin'],job['work_operation'])
            return self.origin_issue(job['plugin_origin'],job['scene_id']) if job['plugin_origin'] else None
        except (ValueError,KeyError) as error:
            return str(error)

    def work_details(self,job):
        origin=job['plugin_origin']
        entry=self.runtime.config_store.catalog.entries.get(origin['plugin_id']) if origin else None
        view={'plugin_name':entry.spec.name if entry else None,'plugin_issue':self.work_issue(job),
            'work_parameters':job['work_parameters'],'work_progress':None,'work_revision_schema':None,
            'work_parameters_schema':None,'work_progress_schema':None}
        try:work=self.work_spec(origin,job['work_operation'])
        except ValueError:return view
        if work:
            view['work_progress']=work.project_progress(work.progress_model.model_validate(job['work_progress']))
            view['work_revision_schema']=work.revision_model.model_json_schema()
            view['work_parameters_schema']=work.parameters_model.model_json_schema()
            view['work_progress_schema']=work.progress_model.model_json_schema()
        return view

    def search_tools(self, query: str, call_context: PluginCallContext, *, kind: Literal["read", "proposal"] = "read") -> tuple[list[dict[str, Any]], list[str]]:
        """Rank the same scoped candidates used for model definitions/execution."""
        candidates = [tool for name, tool in self._tools.items()
                      if self.has_tool(name, call_context) and tool.kind == kind]
        matches = []
        for tool in candidates:
            plugin_name = self._plugins[tool.plugin_id].manifest.name
            score = rank_discovery(query, name=tool.name, aliases=tool.aliases,
                keywords=(*tool.keywords, plugin_name), description=f"{tool.purpose} {tool.description}")
            if score is None:
                continue
            schema = tool.parameter_model.model_json_schema()
            hints = []
            for name, field in schema.get('properties', {}).items():
                hint = {'name': name, 'required': name in schema.get('required', ())}
                if field.get('description'):
                    hint['description'] = field['description']
                if 'default' in field:
                    hint['default'] = field['default']
                hints.append(hint)
            matches.append((score, {'name': tool.name, 'purpose': tool.purpose, 'parameters': hints}))
        matches.sort(key=lambda row: tuple(-value for value in row[0]) + (row[1]['name'],))
        return [item for _, item in matches], sorted({tool.purpose for tool in candidates})

    def proposal_tool_names(self) -> set[str]:
        return {name for name, tool in self._tools.items() if tool.kind == "proposal"}

    def get_tool_definitions(self, call_context: PluginCallContext, *, kind: Literal["read", "proposal"] | None = None) -> list[dict[str, Any]]:
        defs = []
        for name, ptool in self._tools.items():
            if self.has_tool(name, call_context) and (kind is None or ptool.kind == kind):
                defs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"[{ptool.plugin_id}] {ptool.purpose}。{ptool.description}",
                        "parameters": ptool.parameter_model.model_json_schema()
                    }
                })
        return defs

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any], call_context: PluginCallContext) -> ToolResult | dict[str, Any]:
        """The plugin boundary records execution failures as failed observations."""
        ptool = self._tools.get(tool_name)
        if not ptool:
            return ToolResult.failure(f"Tool '{tool_name}' not found.", "not_found", stage='availability',
                tool_name=tool_name, tool_call_id=call_context.tool_call_id)

        if not self.has_tool(tool_name, call_context):
            return ToolResult.failure(f"Plugin tool '{tool_name}' is not available for this scene and role.", "capability_denied",
                stage='availability', tool_name=tool_name, tool_call_id=call_context.tool_call_id)

        source_id=call_context.source_event_id
        if not source_id:
            return ToolResult.failure('Plugin tool invocation has no real source event','invalid_source',
                stage='availability',tool_name=tool_name,tool_call_id=call_context.tool_call_id)
        parent=call_context.origin
        origin=PluginOrigin(plugin_id=ptool.plugin_id,plugin_version=self._plugins[ptool.plugin_id].manifest.version,
            entry_id=tool_name,entry_kind='tool',run_id=f'plugin:{uuid.uuid4().hex}',source_event_id=source_id,
            parent_run_id=call_context.job_id or (parent.run_id if parent else call_context.episode_id),
            parent_tool_call_id=call_context.tool_call_id,scene_entry=call_context.entry,
            handler_origin=call_context.entry_origin if call_context.entry_origin and call_context.entry_origin.entry_kind=='handler' else None)
        try:
            parsed = ptool.parameter_model.model_validate_json(json.dumps(arguments, ensure_ascii=False), strict=True)
        except ValidationError as error:
            return ToolResult.validation_failure(error, tool_name=tool_name, tool_call_id=call_context.tool_call_id).model_copy(update={'plugin_origin':origin})
        try:
            self.record_plugin_run(ptool.plugin_id)
            bound_call = replace(call_context, origin=origin, plugin=self._plugin_contexts[ptool.plugin_id])
            task = self.start_task(ptool.plugin_id, ptool.handler(parsed, bound_call), name=f'tool:{tool_name}',scene_id=call_context.scene_id)
            result = await asyncio.wait_for(
                task,
                timeout=ptool.timeout_seconds
            )
            if ptool.kind == 'proposal' and isinstance(result, dict):
                return {**result,'plugin_origin':origin.model_dump()}
            if not isinstance(result, ToolResult):
                raise TypeError(f"Plugin tool '{tool_name}' must return ToolResult")
            result.plugin_origin = origin
            return result.error_context(tool_name, call_context.tool_call_id)
        except httpx.TimeoutException as error:
            result = ToolResult.failure(f"{type(error).__name__}: {error}；本次来源请求超时，未取得结果；不表示整个能力永久不可用。", 'timeout',
                sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
            result.evidence_kind='external'
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            result = ToolResult.failure(f'来源返回 HTTP {code}：{error}', 'not_found' if code in {404,410} else 'http_error',
                http_status=code, sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
            result.evidence_kind='external'
        except httpx.RequestError as error:
            result = ToolResult.failure(f'{type(error).__name__}: {error}；本次网络请求失败，未取得来源。','network_error',
                sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
            result.evidence_kind='external'
        except asyncio.TimeoutError:
            result = ToolResult.failure(f"Plugin tool '{tool_name}' timed out after {ptool.timeout_seconds}s.", "timeout", stage='execution')
        except ValidationError as error:
            result = ToolResult.validation_failure(error, tool_name=tool_name, tool_call_id=call_context.tool_call_id,
                stage='execution', code='invalid_result')
        except ValueError as error:
            result = ToolResult.failure(f"{type(error).__name__}: {error}", "request_failed", stage='execution')
        except Exception as e:
            result = ToolResult.failure(f"Plugin tool '{tool_name}' execution failed: {type(e).__name__} ({e}).", type(e).__name__, stage='execution')
        result.plugin_origin=origin
        result = result.error_context(tool_name, call_context.tool_call_id)
        self.record_plugin_error(ptool.plugin_id, result.content)
        return result
