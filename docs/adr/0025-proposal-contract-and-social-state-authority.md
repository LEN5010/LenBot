# ADR-0025: Proposal Contract, Typed Social State, and Scoped OpenLoop Authority

## Status

Partially superseded by ADR-0032/ADR-0033 — 2026-09-05. Decision 1's typed `ThreadTransition`/`SocialStateProposal` contract and `STATE_ANNOTATION` events were removed with the ParticipationThread lifecycle; social state authority moved to `GroupAgentSession` snapshots. The scoped atomic open-loop resolution and the unified memory-write validation (`memory/writes.py`) remain active.

## Context

In V2:
1. `state_annotations: dict[str, Any]` was an untyped dictionary passed directly from cognition outcomes into `SceneReducer`. If the model emitted arbitrary keys or illegal string values (e.g. invalid `thread_status`), `ThreadStatus()` threw `ValueError`, crashing the `SceneActor` single-writer loop.
2. `EventStore.commit_proposal_transaction` contained a hazardous fallback: if `UPDATE open_loops ... WHERE id=? AND scene_id=?` matched 0 rows, it executed `UPDATE open_loops ... WHERE id=?` without `scene_id`, allowing a cognition episode in one scene to unauthorizedly mutate open loops belonging to another scene.
3. Memory validation and slot conflict resolution were duplicated across `EventStore` inline SQL, `MemoryGate`, and reflection paths, with subtle divergence (e.g. `save_memory` silently dropping evidence updates during upsert).

## Decision

1. **Typed SocialStateProposal**:
   - Define `ThreadTransition(StrEnum)`: `KEEP`, `FADE`, `CLOSE`.
   - Define `SocialStateProposal(BaseModel)`: `topic: Optional[str] = None`, `thread_transition: Optional[ThreadTransition] = None`.
   - Replace `EpisodeOutcome.state_annotations` with `social_state_proposal: Optional[SocialStateProposal] = None`.
   - `GateDecision` includes `accepted: bool`. When rejected (stale/cancelled/failed), zero side effects occur: no tasks, no loops resolved, no memories saved, no social state applied, no actions queued.
   - On gate accepted, `SceneActor` emits a `STATE_ANNOTATION` event with typed metadata payload. `SceneReducer` strictly processes `topic` and `thread_transition`.

2. **Strict OpenLoop Scene Scoping**:
   - Eliminate the id-only fallback completely.
   - `UPDATE open_loops SET status='resolved' WHERE id=? AND scene_id=? AND status='active'`.
   - If `rowcount == 0`, raise `ValueError`, rolling back the entire atomic transaction.

3. **Unified Memory Writes (`memory/writes.py`)**:
   - Introduce `validate_memory_proposal(db, mp, scene_id)`:
     * Non-empty evidence, deduplicated.
     * Evidence must exist in this scene's `events` or `episodes`.
     * Valid `MemoryKind` and non-empty key.
     * Enforce `mp.scope = scene_id`.
   - Introduce `commit_memory_proposal_core(db, mp, scene_id, now)`:
     * Resolves slot conflicts (`subject, kind, key, scope`) atomically via pure SQL without premature commit.
     * Shares identical semantics across transactional commit, `MemoryGate`, and reflection batch commits.

## Consequences

- No untyped dictionaries can infiltrate `SceneReducer`. Illegal enum strings are rejected before entering runtime state.
- Cross-scene OpenLoop escalation is mathematically impossible: any cross-scene loop id causes atomic transaction rollback.
- Memory evidence provenance and slot resolution logic is unified in a single module.
