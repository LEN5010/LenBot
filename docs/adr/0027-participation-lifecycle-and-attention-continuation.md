# ADR-0027: Participation Thread Lifecycle, Attention Continuation, and Read-Only Scene Detail

## Status

Superseded by ADR-0032/ADR-0033 — 2026-09-05. The `ParticipationThread` fade/close lifecycle and the attention continuation rules were removed with the V3 attention module; participation and thread continuity are judged by the `SocialCognitionCore` inside `GroupAgentSession` state. The read-only scene detail half remains accurate.

## Context

In V2:
1. In `SceneReducer.reduce`:
   When checking thread timeout:
   ```python
   if time_since_rel > 120.0:
       new_state.current_thread.status = ThreadStatus.FADING
   elif time_since_rel > 300.0:
       new_state.current_thread.status = ThreadStatus.CLOSED
   ```
   Because `time_since_rel > 120.0` was checked first, `elif time_since_rel > 300.0` was unreachable dead code. Moreover, `last_relevant_at` was updated unconditionally for any message by a participant, resetting the timeout even when participants were discussing unrelated topics.
2. In `AttentionEngine`:
   Continuation WAKE evaluated `is_participant or has_topic_match or is_immediate_adjacency`. Thus, merely being a past participant in a thread was sufficient to trigger a WAKE on off-topic chatter, violating the anti-chatterbox principle.
3. Thread status had no temporal decay without inbound messages. If a group went completely quiet, the thread remained permanently `ACTIVE` until a new message arrived hours later.
4. In `RuntimeQueryService.scene_detail`:
   Reading a scene invoked `scene_manager.get_or_create_actor(scene_id)`, causing a read query to mutate runtime state by spawning background actors. Furthermore, accessing `current_thread.bot_consecutive_messages` caused an `AttributeError` crashing the scene detail view.

## Decision

1. **Explicit Lifecycle Thresholds in Reducer**:
   - Define constants: `THREAD_FADE_SECONDS = 120.0`, `THREAD_CLOSE_SECONDS = 300.0`.
   - In `SceneReducer`: check `CLOSE` before `FADING`.
   - Update `last_relevant_at` and `last_relevant_event_id` ONLY when an event is truly relevant:
     * Bot mentions or replies.
     * Private messages.
     * Bot outbound messages (`MESSAGE_SENT`).
     * Explicit topic keyword lexical match.
   - If an event is from a participant but not topic-relevant, increment `intervening_messages` without resetting `last_relevant_at`.

2. **Attention Continuation Semantics**:
   - To WAKE on thread continuation without `@`, require `topic_match OR immediate_adjacency`.
   - Participant membership alone without topic match or adjacency yields `AttentionDisposition.OBSERVE` with reason `participant_off_topic`.

3. **Background Thread Temporal Decay**:
   - In `AgentRuntime._maintenance_loop`, scan active actors with `current_thread` and apply time decay against the injected or system clock: transition `ACTIVE` -> `FADING` after 120s and `CLOSED` after 300s.

4. **Read-Only Scene Detail & Field Fix**:
   - `RuntimeQueryService.scene_detail` queries `event_store.load_scene_state(scene_id)` directly without calling `get_or_create_actor`.
   - Correctly read `consecutive_bot_messages` from `SceneState` rather than `ParticipationThread`.

## Consequences

- Participation threads cleanly degrade and close over time without getting stuck.
- Bots do not wake on off-topic conversation between past thread participants.
- Control plane scene detail queries are strictly idempotent and read-only.
