import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Literal
from len_bot.plugins.models import PluginCallContext, PluginToolDefinition
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.tools.results import ToolResult

logger = logging.getLogger(__name__)


@dataclass
class PluginRuntimeStatus:
    """Actual plugin lifecycle and execution status for the control panel."""
    state: str = "loaded"  # loaded / enabled / disabled / error
    last_error: str = ""
    error_count: int = 0
    last_event_at: float = 0.0
    last_run_at: float = 0.0


RESERVED_CORE_TOOLS: frozenset[str] = frozenset({
    "search_messages",
    "read_context",
    "query_timeline",
    "query_person_history",
    "query_memory",
    "tool_search", "read_tool_result", "read_media", "read_web_media", "calculate", "finite_check", "start_work", "discard_proposal", "revise_work", "cancel_work", "resume_work", "schedule_reminder", "update_reminder", "cancel_reminder", "remember", "refute_memory", "supersede_memory", "resolve_wait", "finish_turn", "finish_work", "report_progress", "search_media", "query_jobs",
})


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
        parameters: dict[str, Any],
        handler: Callable[[dict[str, Any], PluginCallContext], Awaitable[ToolResult]],
        timeout_seconds: float,
        *,
        kind: Literal["read", "proposal"],
        roles: tuple[Literal["conversation", "work"], ...],
        deferred: bool = False,
    ) -> None:
        if name in RESERVED_CORE_TOOLS:
            raise ValueError(
                f"Cannot register tool '{name}': tool name is reserved for core agent retrieval."
            )
        self._tools[name] = PluginToolDefinition(
            plugin_id=plugin_id,
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            timeout_seconds=timeout_seconds, kind=kind, roles=roles, deferred=deferred,
        )
        logger.info("Plugin '%s' registered tool '%s'", plugin_id, name)

    async def load_plugin(self, plugin: BasePlugin) -> None:
        pid = plugin.manifest.id
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
        return bool(
            plugin and plugin.manifest.enabled and configured and configured.enabled
            and configured.config is not None and call_context.role in ptool.roles
            and self.runtime.scene_policy.plugin_allowed(call_context.scene_id, ptool.plugin_id, call_context.role)
            and self.runtime.scene_policy.chat_allowed(call_context.scene_id, call_context.requester_qq_uid)
            and (ptool.kind == "read" or call_context.ledger is not None)
        )

    def get_tool_names(self, call_context: PluginCallContext) -> list[str]:
        return [
            name for name, ptool in self._tools.items()
            if self.has_tool(name, call_context)
        ]

    def tool_capabilities(self, name: str) -> dict:
        tool = self._tools[name]
        return {"kind": tool.kind, "roles": tool.roles, "deferred": tool.deferred}

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
                        "description": f"[{ptool.plugin_id}] {ptool.description}",
                        "parameters": ptool.parameters
                    }
                })
        return defs

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any], call_context: PluginCallContext) -> ToolResult:
        """The plugin boundary records execution failures as failed observations."""
        ptool = self._tools.get(tool_name)
        if not ptool:
            return ToolResult.failure(f"Tool '{tool_name}' not found.", "not_found")

        if not self.has_tool(tool_name, call_context):
            return ToolResult.failure(f"Plugin tool '{tool_name}' is not available for this scene and role.", "capability_denied")

        try:
            self.record_plugin_run(ptool.plugin_id)
            result = await asyncio.wait_for(
                ptool.handler(arguments, call_context),
                timeout=ptool.timeout_seconds
            )
            if not isinstance(result, ToolResult):
                raise TypeError(f"Plugin tool '{tool_name}' must return ToolResult")
            return result
        except asyncio.TimeoutError:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' timed out after {ptool.timeout_seconds}s")
            logger.error("Tool '%s' from plugin '%s' timed out after %.1fs", tool_name, ptool.plugin_id, ptool.timeout_seconds)
            return ToolResult.failure(f"Plugin tool '{tool_name}' timed out after {ptool.timeout_seconds}s.", "timeout")
        except Exception as e:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' crashed: {type(e).__name__} ({e})")
            logger.exception("Tool '%s' from plugin '%s' crashed: %s", tool_name, ptool.plugin_id, e)
            return ToolResult.failure(f"Plugin tool '{tool_name}' execution failed: {type(e).__name__} ({e}).", type(e).__name__)
