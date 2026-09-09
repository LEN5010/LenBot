import asyncio
import logging
import json
import time
import httpx
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Literal
from pydantic import BaseModel, ValidationError
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginToolDefinition
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
            timeout_seconds=timeout_seconds, kind=kind, roles=roles, deferred=deferred, available=available,
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
        plugin = self._plugins.pop(plugin_id, None)
        if plugin is not None:
            try:
                await plugin.on_unload()
            except Exception as error:
                self.record_plugin_error(plugin_id, f'unload failed: {error}')
        self._plugin_contexts.pop(plugin_id, None)
        for name in [name for name, tool in self._tools.items() if tool.plugin_id == plugin_id]:
            del self._tools[name]

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
        if self._status[plugin_id].state == 'enabled':
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
            await plugin.on_disable()
        except Exception as error:
            self.record_plugin_error(plugin_id, f'disable failed: {error}')
            raise
        self._status[plugin_id].state = 'disabled'

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

    def get_plugin(self, plugin_id: str) -> BasePlugin | None:
        return self._plugins.get(plugin_id)

    def get_plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Current plugin parameters from the root configuration."""
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            raise KeyError(plugin_id)
        return dict(plugin.manifest.config)

    def set_plugin_config(self, plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
        """Publish parameters already parsed by the root configuration boundary."""
        self._plugins[plugin_id].manifest.config = dict(config)
        return dict(config)

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
                'plugin_type': spec.plugin_type.value,
                'permissions': [permission.value for permission in spec.permissions],
                'enabled': bool(plugin and plugin.manifest.enabled), 'state': status.state,
                'last_error': status.last_error, 'error_count': status.error_count,
                'last_event_at': status.last_event_at, 'last_run_at': status.last_run_at,
                'config': saved.config if saved else None,
                'config_schema': spec.config_model.model_json_schema(),
                'emitted_events': plugin.manifest.emitted_events if plugin else [],
                'registered_tools': [name for name, tool in self._tools.items() if tool.plugin_id == plugin_id],
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
        requesters = call_context.requester_qq_uids if call_context.role == 'conversation' else (call_context.requester_qq_uid,)
        if (not self.runtime.scene_policy.plugin_allowed(call_context.scene_id, plugin_id, call_context.role)
                or not any(self.runtime.scene_policy.chat_allowed(call_context.scene_id, requester) for requester in requesters)):
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
        return {"kind": tool.kind, "roles": tool.roles, "deferred": tool.deferred}

    def search_tools(self, query: str, call_context: PluginCallContext, *, kind: Literal["read", "proposal"] = "read",
                     excluded: set[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        """Rank the same scoped candidates used for model definitions/execution."""
        candidates = [tool for name, tool in self._tools.items()
                      if self.has_tool(name, call_context) and tool.kind == kind and name not in (excluded or ())]
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

        try:
            parsed = ptool.parameter_model.model_validate_json(json.dumps(arguments, ensure_ascii=False), strict=True)
        except ValidationError as error:
            return ToolResult.validation_failure(error, tool_name=tool_name, tool_call_id=call_context.tool_call_id)
        try:
            self.record_plugin_run(ptool.plugin_id)
            result = await asyncio.wait_for(
                ptool.handler(parsed, call_context),
                timeout=ptool.timeout_seconds
            )
            if ptool.kind == 'proposal' and isinstance(result, dict):
                return result
            if not isinstance(result, ToolResult):
                raise TypeError(f"Plugin tool '{tool_name}' must return ToolResult")
            return result.error_context(tool_name, call_context.tool_call_id)
        except httpx.TimeoutException as error:
            result = ToolResult.failure(f"{type(error).__name__}: {error}；本次来源请求超时，未取得结果；不表示整个能力永久不可用。", 'timeout',
                sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            result = ToolResult.failure(f'来源返回 HTTP {code}：{error}', 'not_found' if code in {404,410} else 'http_error',
                http_status=code, sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
        except httpx.RequestError as error:
            result = ToolResult.failure(f'{type(error).__name__}: {error}；本次网络请求失败，未取得来源。','network_error',
                sources=[ToolSource(url=error_source_url(str(error.request.url)))], stage='execution')
        except asyncio.TimeoutError:
            result = ToolResult.failure(f"Plugin tool '{tool_name}' timed out after {ptool.timeout_seconds}s.", "timeout", stage='execution')
        except ValidationError as error:
            result = ToolResult.validation_failure(error, tool_name=tool_name, tool_call_id=call_context.tool_call_id,
                stage='execution', code='invalid_result')
        except ValueError as error:
            result = ToolResult.failure(f"{type(error).__name__}: {error}", "request_failed", stage='execution')
        except Exception as e:
            result = ToolResult.failure(f"Plugin tool '{tool_name}' execution failed: {type(e).__name__} ({e}).", type(e).__name__, stage='execution')
        result = result.error_context(tool_name, call_context.tool_call_id)
        self.record_plugin_error(ptool.plugin_id, result.content)
        return result
