import asyncio
import logging
import json
import time
import httpx
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Literal
from pydantic import BaseModel, ValidationError
from len_bot.plugins.models import PluginCallContext, PluginToolDefinition
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.tools.results import ToolResult
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
    from len_bot.cognition.proposals import FINISH_TURN, TOOLS, definition
    from len_bot.runtime.job_runner import FINISH_WORK, REPORT_PROGRESS, SKILL_TOOLS, UPDATE_WORK_STATE
    from len_bot.skills.learning import MAINTENANCE_TOOLS
    from len_bot.tools.retrieval import CORE_READ_TOOLS

    groups = {
        "core:retrieval": CORE_READ_TOOLS,
        "core:proposals": [FINISH_TURN, *(definition(name, *spec) for name, spec in TOOLS.items())],
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
        logger.info("Plugin '%s' registered tool '%s'", plugin_id, name)

    async def load_plugin(self, plugin: BasePlugin) -> None:
        pid = plugin.manifest.id
        if pid in self._plugins:
            existing = self._plugins[pid]
            raise ValueError(f"Plugin '{pid}' registration conflict: {type(plugin).__module__}.{type(plugin).__name__} "
                             f"conflicts with {type(existing).__module__}.{type(existing).__name__}.")
        self._plugins[pid] = plugin
        ctx = PluginContext(plugin.manifest, self.runtime, self)
        self._plugin_contexts[pid] = ctx
        self._status[pid] = PluginRuntimeStatus(
            state="enabled" if plugin.manifest.enabled else "disabled"
        )
        try:
            await plugin.on_load(ctx)
            logger.info("Loaded plugin '%s' (%s)", pid, plugin.manifest.name)
        except Exception as e:
            logger.exception("Failed to load plugin '%s': %s", pid, e)
            self.record_plugin_error(pid, f"load failed: {e}")
            await self.unload_plugin(pid)
            raise

    async def unload_plugin(self, plugin_id: str) -> None:
        plugin = self._plugins.pop(plugin_id, None)
        if plugin:
            try:
                await plugin.on_unload()
            except Exception as e:
                logger.warning("Error during on_unload for plugin '%s': %s", plugin_id, e)
        self._plugin_contexts.pop(plugin_id, None)
        self._status.pop(plugin_id, None)

        # Clean up registered tools
        tools_to_remove = [k for k, v in self._tools.items() if v.plugin_id == plugin_id]
        for t in tools_to_remove:
            self._tools.pop(t, None)

        logger.info("Unloaded plugin '%s' and cleaned up its tools", plugin_id)

    async def unload_all(self) -> None:
        for pid in list(self._plugins.keys()):
            await self.unload_plugin(pid)

    async def enable_plugin(self, plugin_id: str) -> None:
        plugin = self._plugins.get(plugin_id)
        if plugin:
            plugin.manifest.enabled = True
            self._status[plugin_id].state = "enabled"
            try:
                await plugin.on_enable()
            except Exception as e:
                self.record_plugin_error(plugin_id, f"on_enable failed: {e}")

    async def disable_plugin(self, plugin_id: str) -> None:
        plugin = self._plugins.get(plugin_id)
        if plugin:
            plugin.manifest.enabled = False
            self._status[plugin_id].state = "disabled"
            try:
                await plugin.on_disable()
            except Exception as e:
                self.record_plugin_error(plugin_id, f"on_disable failed: {e}")

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
        """Control panel projection of the plugins actually loaded."""
        out = []
        for pid, plugin in self._plugins.items():
            status = self._status.get(pid, PluginRuntimeStatus())
            m = plugin.manifest
            out.append({
                "id": pid,
                "name": m.name,
                "description": m.description,
                "version": m.version,
                "plugin_type": m.plugin_type.value,
                "permissions": [p.value for p in m.permissions],
                "enabled": m.enabled,
                "state": status.state,
                "last_error": status.last_error,
                "error_count": status.error_count,
                "last_event_at": status.last_event_at,
                "last_run_at": status.last_run_at,
                "config": m.config,
                "config_schema": m.config_schema,
                "emitted_events": m.emitted_events,
                "registered_tools": m.registered_tools,
                "source_status": plugin.source_status(),
            })
        return out

    def has_tool(self, name: str, call_context: PluginCallContext) -> bool:
        ptool = self._tools.get(name)
        if not ptool:
            return False
        plugin = self._plugins.get(ptool.plugin_id)
        configured = self.runtime.config_store.current.plugins.get(ptool.plugin_id)
        requesters = call_context.requester_qq_uids if call_context.role == 'conversation' else (call_context.requester_qq_uid,)
        return bool(
            plugin and plugin.manifest.enabled and configured and configured.enabled
            and configured.config is not None and call_context.role in ptool.roles
            and self.runtime.scene_policy.plugin_allowed(call_context.scene_id, ptool.plugin_id, call_context.role)
            and any(self.runtime.scene_policy.chat_allowed(call_context.scene_id, requester) for requester in requesters)
            and (ptool.kind == "read" or call_context.ledger is not None)
            and (ptool.available is None or ptool.available(call_context))
        )

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
            return ToolResult.failure(f"Tool '{tool_name}' not found.", "not_found")

        if not self.has_tool(tool_name, call_context):
            return ToolResult.failure(f"Plugin tool '{tool_name}' is not available for this scene and role.", "capability_denied")

        try:
            parsed = ptool.parameter_model.model_validate_json(json.dumps(arguments, ensure_ascii=False), strict=True)
        except ValidationError as error:
            return ToolResult.failure(f"{tool_name}: {error}", "invalid_arguments")
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
            return result
        except httpx.TimeoutException:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' network request timed out")
            return ToolResult.failure(f"{tool_name}: 本次来源请求超时，未取得结果；不表示整个能力永久不可用。", 'timeout')
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' upstream returned HTTP {code}")
            return ToolResult.failure(f'{tool_name}: 来源返回 HTTP {code}。', 'not_found' if code in {404,410} else 'http_error')
        except httpx.RequestError:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' network request failed")
            return ToolResult.failure(f'{tool_name}: 本次网络请求失败，未取得来源。','network_error')
        except asyncio.TimeoutError:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' timed out after {ptool.timeout_seconds}s")
            logger.error("Tool '%s' from plugin '%s' timed out after %.1fs", tool_name, ptool.plugin_id, ptool.timeout_seconds)
            return ToolResult.failure(f"Plugin tool '{tool_name}' timed out after {ptool.timeout_seconds}s.", "timeout")
        except ValueError as error:
            return ToolResult.failure(f"{tool_name}: {error}", "request_failed")
        except Exception as e:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' crashed: {type(e).__name__} ({e})")
            logger.exception("Tool '%s' from plugin '%s' crashed: %s", tool_name, ptool.plugin_id, e)
            return ToolResult.failure(f"Plugin tool '{tool_name}' execution failed: {type(e).__name__} ({e}).", type(e).__name__)
