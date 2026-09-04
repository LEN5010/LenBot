# ADR-0028: Reflection Provider Wiring, Batch Cursor Ingestion, and Atomic Batch Commit

## Status

Partially superseded — 2026-09-05. §4's once-only postpone bound (`_reflection_postpone_counts`, keyed on the removed `bot_engagement` concept) was not retained: the quiet-window timer re-arms while a cognition episode is in flight (see ADR-0019's in-flight postponement). Provider wiring, `get_unreflected_events(after_rowid, limit=30)`, and atomic `commit_reflection_batch` remain active.

## Context

In V2:
1. In `AgentRuntime.start()`:
   `LLMReflector.from_registry(self.provider_registry)` was initialized before `provider_registry.apply_update()` loaded persisted provider configs from SQLite. As a result, `provider_registry.has_live_provider()` was always False at boot, permanently locking production reflection into deterministic fallback mode.
2. In reflection event fetching:
   `get_events_since(scene_id, after_rowid, limit=200)` fetched up to 200 events, but reflector only consumed the trailing 30 events, and then advanced the cursor to `max(rowid)` of all 200 events. The oldest 170 events were permanently skipped and never reflected upon.
3. In reflection persistence:
   Episode was inserted first; memory proposals were validated/committed in separate calls; if any memory proposal was invalid, the episode had already been persisted and the cursor moved or threw errors leaving partial writes in SQLite.
4. Quiet window trigger:
   Postponed indefinitely while `bot_engagement == "active"`, but engagement decay only triggered on new messages. Quiet scenes with stuck engagement would never reflect. Moreover, `actor.has_active_episode()` was not checked.

## Decision

1. **Deterministic Startup Wiring Order**:
   - In `AgentRuntime.start()`, load and apply `provider_config` into `provider_registry` BEFORE constructing `LLMReflector.from_registry`. If a valid provider is configured, reflection uses the live LLM provider.

2. **Batch Cursor Ingestion (`get_unreflected_events`)**:
   - Add `EventStore.get_unreflected_events(scene_id: str, after_rowid: int, limit: int = 30) -> list[Event]` querying `WHERE scene_id = ? AND rowid > ? ORDER BY rowid ASC LIMIT ?`.
   - The reflector strictly consumes the exact batch returned (up to 30 events), and the cursor advances to the max `rowid` of that exact consumed batch. Zero events are skipped.

3. **Atomic Reflection Batch Commit**:
   - Add `EventStore.commit_reflection_batch(scene_id: str, episode_record: EpisodeRecord, proposals: list[MemoryProposal], new_cursor_rowid: int)` executing inside a single SQLite transaction under `_write_lock`:
     a. Insert `EpisodeRecord` into `episodes` table.
     b. Validate and commit each `MemoryProposal` via `validate_memory_proposal` and `commit_memory_proposal_core` with `scope = scene_id`.
     c. Upsert `reflection_cursors` with `new_cursor_rowid`.
   - If any step fails, entire transaction is rolled back and `reflection_cursors` remains at the previous rowid.

4. **Quiet Window Gating & Bound Postpone**:
   - In `_quiet_window_reflect`:
     * Check `actor.has_active_episode()`. If active, postpone reflection.
     * Track postpone count per scene (`_reflection_postpone_counts[scene_id]`). Postpone at most once for `bot_engagement == "active"`; on second attempt, proceed with reflection unless an active episode is running.

## Consequences

- Real LLM reflection executes in production whenever a provider is configured.
- Reflection is gap-free, bounded to batches of 30, and never skips older events.
- Atomic commit guarantees that episode records, memories, and cursor advances are strictly all-or-nothing.
