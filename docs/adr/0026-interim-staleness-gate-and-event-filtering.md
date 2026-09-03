# ADR-0026: Interim Event Filtering, Staleness Gating, and Human Message Accounting

## Context

In V2:
1. `EpisodeMailbox.post()` accepted all event types unconditionally, including internal events like `STATE_ANNOTATION`, `TASK_DUE`, and sensor facts. These polluted interim event lists with synthetic traffic.
2. `EpisodeMailbox` had no non-destructive way to check if new interim events arrived (`has_unseen_interim()`), forcing either destructive consumption or missing interim updates at the step boundary.
3. If an episode was in a multi-step tool call and scene conversation moved on (e.g. another human answered the query, or new human messages advanced `scene_state.version`), the final outcome might commit stale answers.
4. If ReAct steps reached maximum steps without emitting a final response, it could fail open or crash. The final step must fail closed to SILENCE.
5. In `RuntimeMetrics`, `human_messages` was incremented for every raw event or message sent, distorting social chatter metrics by counting bot self-echoes and system annotations as human messages.

## Decision

1. **Mailbox Event Filtering & Non-destructive Check**:
   - `EpisodeMailbox.post(event)` accepts only `GROUP_MESSAGE_RECEIVED` and `PRIVATE_MESSAGE_RECEIVED`. All other events (e.g. `STATE_ANNOTATION`, `TASK_DUE`, `MESSAGE_SENT`, `MESSAGE_SEND_FAILED`) are silently ignored.
   - Add `has_unseen_interim() -> bool` returning `len(self._interim_events) > self._cursor`.
   - At ReAct step boundaries in `PiAgentCore`, evaluate staleness against unseen interim messages.
   - If max ReAct iterations are exhausted without a structured outcome, fail-closed to `FinalDisposition.SILENCE`.

2. **RuntimeGate Version & Freshness Check**:
   - In `RuntimeGate.evaluate_and_commit`:
     If `current_scene_state.version > mailbox.base_scene_version`:
     Inspect `mailbox.get_interim_events()`: if any human message has arrived since the episode began, evaluate whether the response is stale. If stale or cancelled, return `GateDecision(FinalDisposition.SILENCE, "Gate rejected stale response due to interim scene advancement", accepted=False)`.

3. **Accurate Human Message Accounting**:
   - Only increment `metrics.inc_social("human_messages")` when receiving `GROUP_MESSAGE_RECEIVED` or `PRIVATE_MESSAGE_RECEIVED` where `event.actor_id != bot_actor_id`.
   - System events, bot outbound echoes, and plugin facts never count towards `human_messages`.

## Consequences

- Cognition mailboxes are clean and contain only genuine conversational messages.
- Stale cognitive outcomes generated during long tool executions are safely intercepted before committing durable side effects.
- Metrics accurately reflect real human conversation volume and bot participation ratio.
