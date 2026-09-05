# ADR-0030: Plugin Lifecycle Hooks, Core Tool Namespace Reservation, and SSRF Network Policy

## Status

Accepted — implementation notes 2026-09-05: the registration API is `PluginHost.register_plugin_tool(...)`; `RESERVED_CORE_TOOLS` also includes `inspect_episode` (ADR-0035); SSRF-blocked tool responses return the structured string `[安全拦截: 目标地址受限 - {reason}]` instead of raising.

## Context

In V2:
1. `BilibiliLiveSensor` had stub/initial lifecycle hooks, but when disabled via PluginHost or Cockpit API, background polling tasks could remain orphaned or fail to restart cleanly upon re-enabling.
2. Third-party or user-provided plugins could register tools with any name via `PluginHost.register_tool()`. A malicious or buggy plugin could overwrite core agent retrieval tools (`search_messages`, `read_context`, etc.), bypassing ExecutionScope privacy checks or crashing cognition.
3. The `read_page` / `web_search` tools had basic error handling, but lacked explicit SSRF (Server-Side Request Forgery) protection against private IP ranges (`127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`, `169.254.169.254`, `localhost`, etc.), allowing prompt injections to probe internal networks or cloud metadata services.

## Decision

1. **Plugin Lifecycle Enforcement**:
   - In `BilibiliLiveSensor`:
     * Maintain `_poll_task: Optional[asyncio.Task] = None`.
     * `on_enable()` starts the background polling loop if not already running.
     * `on_disable()` cancels the polling task and awaits its graceful termination.
   - In `PluginHost`:
     * Calling `enable_plugin(id)` awaits `plugin.on_enable()`.
     * Calling `disable_plugin(id)` awaits `plugin.on_disable()`.

2. **RESERVED_CORE_TOOLS Protection**:
   - Define `RESERVED_CORE_TOOLS = frozenset({"search_messages", "read_context", "query_timeline", "query_person_history", "query_memory", "query_open_loops", "query_tasks", "query_retention"})`.
   - In `PluginHost.register_tool(plugin_id, name, handler, schema)`:
     * If `name in RESERVED_CORE_TOOLS`, raise `ValueError(f"Cannot register tool '{name}': tool name is reserved for core agent retrieval.")`.

3. **SSRF Guard with `net_policy.py`**:
   - Create `src/len_bot/plugins/net_policy.py`:
     * Checks URLs against private/loopback/link-local IPv4 and IPv6 subnets, `localhost`, `.local`, `.internal`, and metadata endpoints (e.g. `169.254.169.254`).
     * Resolves DNS hostnames and verifies the resolved IP addresses against blocked CIDR blocks.
   - In `read_page` and `web_search`:
     * Validate target URLs with `net_policy.validate_url(url)`.
     * If blocked, return a structured safety error string (`"[SECURITY BLOCKED: Target address is private or loopback]"`), never throw uncaught exceptions.

## Consequences

- Core retrieval tools cannot be hijacked or overridden by plugins.
- Background sensor tasks cleanly start and stop without coroutine leaks.
- Outbound HTTP tools are protected against internal network scanning and cloud metadata exfiltration.
