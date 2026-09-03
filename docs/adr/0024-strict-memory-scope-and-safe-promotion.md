# ADR-0024: Strict Memory Scope and Safe Promotion

## Context

In V2, epistemic memories supported a `visibility` field (`scene` vs `global`). However, this introduced a dual authority defect:
1. `MemoryStore.query_memories` had `WHERE (scope IN (...) OR visibility = 'global')`.
2. The LLM Reflector prompt exposed `"visibility": "scene|global"` allowing untrusted model output to unilaterally declare memories as global.
3. `commit_proposal_transaction` only enforced `scope = scene_id` but never touched `visibility`.
4. An LLM or malicious prompt could bypass private scene isolation simply by outputting `visibility = 'global'`.

## Decision

1. **Eliminate Memory Visibility**:
   - Delete the `visibility` column and model fields from `MemoryItem` and `MemoryProposal`.
   - `query_memories` strictly enforces `WHERE {status_clause} AND scope IN ({placeholders})`.
   - SQLite migration drops the `visibility` column idempotently.
   - Reflector prompt and parsing completely drop `visibility`.

2. **Safe Global Promotion Authority (`promote_memory`)**:
   - Neither cognition nor reflection has the authority to promote a memory to cross-scene visibility.
   - Global sharing is strictly gated via `MemoryStore.promote_memory(memory_id)` invoked exclusively by human operator intervention via `POST /api/cockpit/memories/{id}/promote`.
   - Promotion creates a **NEW** record with `scope='global-safe'` and human-readable assertion prefixed with `promoted_from:<id>:`. The original record is preserved untouched.

3. **Subtractions**:
   - Delete `PluginPermission.SCHEDULE_TASK` and `PluginContext.schedule_task` (plugins only act as sensors, tools, or interceptors).
   - Delete dead configuration `bot_cooldown_seconds`.
   - Rename `EpisodeOutcome.thought` to `decision_reason`, enforcing structured behavioral reasons rather than ungrounded internal chain-of-thought in traces.
   - Migrate control plane authentication to cookie-only sessions with `dashboard_cookie_secure` configuration.

## Consequences

- Direct prompt injection cannot leak private scene memories to other scenes via `visibility`.
- Memory visibility boundaries are 100% governed by SQL `scope IN (...)`.
- Operator promotion maintains full provenance via `promoted_from:<id>:` evidence lineage.
