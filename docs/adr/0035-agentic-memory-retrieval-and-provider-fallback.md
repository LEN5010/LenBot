# ADR-0035: Agentic Memory Retrieval in Social Core and Provider Fallback Routing

- Status: Accepted
- Date: 2026-09-04
- Owners: LenBot maintainers

## Context

ADR-0033 deliberately shipped the Social Cognition Core without retrieval tools: it saw the `GroupAgentSession` state, up to 80 recent raw events, and active open loops, and nothing else. Cross-day continuity therefore failed whenever a burst referenced people, promises, or topics that predated the recent window. Invariant 8 ("no automatic RAG on every message") ruled out injecting retrieved memory into every context; the missing capability was *on-demand* retrieval under the model's own judgement.

Independently, tier routing (ADR-0020) had exactly one target per tier: a provider outage silently killed every cognition episode, and operators could not discover which models an OpenAI-compatible endpoint actually advertises.

The informal working name "V4 Stage 4" for this batch collided with ADR-0032, which reserves Stage 4 for session context rollover. This ADR supersedes that informal name.

## Decision

**Agentic retrieval loop.** `SocialCognitionCore.execute` runs a bounded ReAct loop (default `max_steps=5`) over its existing context assembly. When a `RetrievalToolkit` is injected (production wiring: `allowed_scopes=[scene_id, "global-safe"]`, `default_scene_id=scene_id`), the model may call the standard ADR-0010/0011 retrieval tools (`search_messages`, `read_context`, `query_person_history`, `query_memory`, `inspect_episode`); tool results re-enter the same message flow, and the final `SocialCognitionResult` must still be produced by the same persona. Retrieval stays on-demand: the system prompt permits tool use only when past context matters, and no automatic retrieval path exists.

**Deterministic failure semantics.** The loop is fail-closed, not fail-dead:

- *Forced convergence.* On the last step, or once the deterministic tool budget (`max_tool_calls=6`) is exhausted, the runtime calls the model with `tool_choice="none"` so the episode must end in a decision instead of raising. A provider that ignores the forced final raises — that is a protocol violation, not a retrieval miss.
- *Tool errors are materials, not aborts.* Malformed tool arguments and toolkit exceptions are fed back to the model as `role:"tool"` error payloads; the model may retry differently or stay silent. Only systemic failures (both providers down, forced final ignored) propagate to the caller.
- *Minimal assistant echo.* Assistant tool-call messages are reconstructed with only `role`/`content`/`tool_calls` — echoing full SDK messages carries provider-specific null fields that strict OpenAI-compatible endpoints reject with 400, precisely when a cross-vendor fallback is in play.

**Observability.** Every step records tier, provider, model, fallback flag, `forced_final`, latency, and token usage in the trace; the trace lands in the `traces` payload and the Trace view reports per-episode tool-call counts. `RuntimeMetrics` gains `retrieval_tool_calls`, `retrieval_tool_errors`, and `retrieval_forced_finals` so "is the model retrieving on every turn?" (the invariant 8 question) is answerable from data, with the deterministic `max_tool_calls` ceiling as the hard backstop.

**Provider fallback and model catalog.** `RoutingConfig` gains an optional `fallback` route target. `resolve(tier)` keeps tier semantics; on a primary failure `_call_model` tries the fallback once (no chains, no returning to the primary), never when it equals the primary target. Data-privacy note: engaging the fallback sends the episode context to a different vendor — configuring a cross-vendor fallback is an explicit operator acknowledgement of that flow. Providers expose a `models` catalog (fetched read-only from the endpoint's `/v1/models`, curated via checkboxes in the Control Plane) that backfills route-target models at startup; fallback routing and catalogs hot-swap through the existing `apply_update` persistence path.

**Naming.** The legacy V3 ReAct loop `PiAgentCore` is renamed `ReActAgentCore` (`cognition/react_core.py`). The "Pi" name referenced an abandoned plan to build on an external agent framework that never entered the codebase; the module remains boundary-test-only and out of the production path. References in ADRs earlier than this one are left as dated records.

## Consequences

- Cognition episodes may now spend multiple LLM calls and retrievals per burst; the budget, step limit, and metrics bound the cost, and the operator accepts the token increase for continuity quality.
- Retrieval scope is still enforced at the SQL layer inside `RetrievalToolkit`; the tool loop adds no new scope authority.
- `ReActAgentCore` remains un-wired to the toolkit; it is test-only surface, and behaviour drift between the two loops is accepted until it is archived or aligned.
- The fallback route is single-hop and tier-agnostic; a failing fallback surfaces as a normal episode failure with both provider errors recorded.
- Trace consumers (Trace view, Replay Lab) see the new `steps` shape; older traces without steps keep rendering.
