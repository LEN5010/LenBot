import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, Awaitable
from len_bot.plugins.models import PluginManifest, PluginToolDefinition
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.actions.models import ActionItem

logger = logging.getLogger(__name__)


@dataclass
class PluginRuntimeStatus:
    """ADR-0021 §15.4: per-plugin health & error state for the Control Plane."""
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
    "query_open_loops",
    "query_tasks",
    "query_retention",
})


class PluginHost:
    """Manages plugin lifecycle, sandboxed tool execution, and action interception (ADR-0016)."""

    def __init__(self, runtime: Any = None):
        self.runtime = runtime
        self._plugins: dict[str, BasePlugin] = {}
        self._plugin_contexts: dict[str, PluginContext] = {}
        self._tools: dict[str, PluginToolDefinition] = {}
        self._interceptors: dict[str, Callable[[ActionItem], Awaitable[Optional[ActionItem]]]] = {}
        self._status: dict[str, PluginRuntimeStatus] = {}

    def register_plugin_tool(
        self,
        plugin_id: str,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[str]],
        timeout_seconds: float = 5.0
    ) -> None:
        if name in RESERVED_CORE_TOOLS:
            raise ValueError(
                f"Cannot register tool '{name}': tool name is reserved for core agent retrieval (ADR-0030, §21.2)."
            )
        self._tools[name] = PluginToolDefinition(
            plugin_id=plugin_id,
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            timeout_seconds=timeout_seconds
        )
        logger.info("Plugin '%s' registered tool '%s'", plugin_id, name)

    def register_action_interceptor(
        self,
        plugin_id: str,
        interceptor: Callable[[ActionItem], Awaitable[Optional[ActionItem]]]
    ) -> None:
        self._interceptors[plugin_id] = interceptor
        logger.info("Plugin '%s' registered action interceptor", plugin_id)

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

        # Clean up interceptors
        self._interceptors.pop(plugin_id, None)
        logger.info("Unloaded plugin '%s' and cleaned up tools/interceptors", plugin_id)

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

    def status_snapshot(self) -> list[dict[str, Any]]:
        """Control Plane view: real registry only — no mock entries (ADR-0021)."""
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
                "default_config": m.default_config,
                "emitted_events": m.emitted_events,
                "registered_tools": m.registered_tools,
            })
        return out

    def has_tool(self, name: str) -> bool:
        ptool = self._tools.get(name)
        if not ptool:
            return False
        plugin = self._plugins.get(ptool.plugin_id)
        return bool(plugin and plugin.manifest.enabled)

    def get_tool_names(self) -> list[str]:
        return [
            name for name, ptool in self._tools.items()
            if self._plugins.get(ptool.plugin_id) and self._plugins[ptool.plugin_id].manifest.enabled
        ]

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        defs = []
        for name, ptool in self._tools.items():
            plugin = self._plugins.get(ptool.plugin_id)
            if plugin and plugin.manifest.enabled:
                defs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"[{ptool.plugin_id}] {ptool.description}",
                        "parameters": ptool.parameters
                    }
                })
        return defs

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Fault-isolated tool execution with timeout guard (Goal 7)."""
        ptool = self._tools.get(tool_name)
        if not ptool:
            return f"Error: Tool '{tool_name}' not found."

        plugin = self._plugins.get(ptool.plugin_id)
        if not plugin or not plugin.manifest.enabled:
            return f"Error: Plugin '{ptool.plugin_id}' is disabled."

        try:
            self.record_plugin_run(ptool.plugin_id)
            result = await asyncio.wait_for(
                ptool.handler(arguments),
                timeout=ptool.timeout_seconds
            )
            return str(result)
        except asyncio.TimeoutError:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' timed out after {ptool.timeout_seconds}s")
            logger.error("Tool '%s' from plugin '%s' timed out after %.1fs", tool_name, ptool.plugin_id, ptool.timeout_seconds)
            return f"Error: Plugin tool '{tool_name}' timed out after {ptool.timeout_seconds}s."
        except Exception as e:
            self.record_plugin_error(ptool.plugin_id, f"Tool '{tool_name}' crashed: {type(e).__name__} ({e})")
            logger.exception("Tool '%s' from plugin '%s' crashed: %s", tool_name, ptool.plugin_id, e)
            return f"Error: Plugin tool '{tool_name}' execution failed: {type(e).__name__} ({e})."

    async def intercept_action(self, action: ActionItem) -> Optional[ActionItem]:
        """Runs action through active interceptors in sequence with fault protection."""
        current_action = action
        for pid, interceptor in list(self._interceptors.items()):
            plugin = self._plugins.get(pid)
            if not plugin or not plugin.manifest.enabled:
                continue
            try:
                current_action = await asyncio.wait_for(interceptor(current_action), timeout=3.0)
                if current_action is None:
                    logger.info("Action %s blocked by plugin interceptor '%s'", action.id, pid)
                    return None
            except Exception as e:
                logger.error("Error in action interceptor from plugin '%s': %s", pid, e)
        return current_action
