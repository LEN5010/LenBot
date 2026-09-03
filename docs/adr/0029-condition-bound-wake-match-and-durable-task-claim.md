# ADR-0029: Condition-Bound wake_match Dict Matching, Durable Task Claim, and Provenance Lineage

## Context

In V2:
1. `TaskItem.wake_event_type` matched only the string `event.event_type`. If two tasks waited for `LIVE_STARTED` in different rooms (e.g. room 123 vs room 456), any live broadcast fired both tasks.
2. In `TaskScheduler`:
   Tasks were popped from in-memory heap and emitted as `TASK_DUE` without atomic, durable database status claim (`claimed`). If multiple scheduler sweeps or concurrent event notifications fired, duplicate `TASK_DUE` events leaked into the runtime.
3. Provenance lineage was partially lost:
   Tasks and open loops did not fully retain `origin_episode_id` and `origin_stimulus_id`, making it difficult to trace which episode and user query spawned a given task or loop in the Control Plane Trace view.

## Decision

1. **Structured `wake_match` on TaskItem & TaskProposal**:
   - Add `wake_match: Optional[dict[str, Any]] = None` to `TaskItem` and `TaskProposal`.
   - In `TaskScheduler.on_event`:
     * Event must match `wake_event_type` and `scene_id`.
     * If `task.wake_match` is specified, every key-value in `wake_match` must match against `event.payload`.
     * If `task.wake_match` is None or empty, fall back to matching `wake_event_type`.

2. **Durable Task Claim**:
   - Add `EventStore.claim_task(task_id: str, trigger_event_id: str) -> bool`:
     Executes `UPDATE tasks SET status = 'claimed', trigger_event_id = ? WHERE id = ? AND status = 'pending'`.
     Only if `cursor.rowcount == 1` does the scheduler proceed to emit `TASK_DUE`.
     If another coroutine or worker already claimed the task, subsequent attempts fail cleanly and emit nothing.

3. **Complete Lineage Propagation**:
   - `TaskItem` and `TaskProposal` retain `origin_episode_id` and `origin_stimulus_id`.
   - `OpenLoop` retains `source_event_id` and `source_stimulus_id`.

4. **Cockpit Task Promote / Trigger API**:
   - Provide `POST /api/cockpit/tasks/{task_id}/promote` to duplicate a task as a permanent/global template or re-arm it safely without violating runtime authority.

## Consequences

- Condition-bound tasks can precisely bind to specific resources (e.g. specific room IDs or user IDs).
- Duplicate execution of scheduled tasks is eliminated via durable SQL status preemption.
- Full provenance is preserved across episodes, tasks, open loops, and actions.
