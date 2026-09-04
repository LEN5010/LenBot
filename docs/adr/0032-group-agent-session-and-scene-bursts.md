# ADR-0032: Persistent Group Agent Session and Scene-Level Bursts

## Status

Accepted — 2026-09-04

## Context

V3 materializes operational scene state, but its cognitive input is built by a per-sender `StimulusBuilder`. That boundary fragments a natural multi-person exchange and leaves no durable representation of the agent's current social understanding. A process restart restores `SceneState`, tasks, loops, and memories, but not the working social context needed for continuity.

V4 needs a persistent per-scene cognitive state without moving social judgement into deterministic runtime code. This stage must establish that state and preserve conversational ordering before the Social Cognition Core replaces V3 attention policy.

## Decision

Each `SceneActor` owns one in-memory `GroupAgentSession` for its scene. The session contains structured placeholders for social world state, self state, group identity, working people and relationships, retained attention, latent expectations, and the next wake intent. Its Stage 1 reducer updates only factual observations: ordered event references, sender snapshots, and sent/human message counters. It does not infer topics, relationships, mood, interest, or whether to speak.

`EventStore.commit_scene_event` is the single commit point for the raw event, `SceneState`, and `GroupAgentSession`. The actor publishes both candidate states only after that transaction commits. `group_agent_sessions.last_observed_event_rowid` is the durable cursor for recovery; `last_cognized_event_rowid` is reserved for the Social Cognition Core introduced in Stage 2.

`BurstAssembler` replaces `StimulusBuilder`. It buffers by `scene_id`, retains every ordered source event, and joins only by short arrival timing. It performs no topic classification, keyword urgency detection, interest scoring, or wake decision. Direct mention/reply is a structural low-latency flush signal. Task and plugin events flush pending chat first, preserving committed scene order.

During Stage 1, the assembled burst feeds the one existing V3 attention/cognition path so observable output behavior remains available while the new state foundation is verified. This is a temporary migration sequence, not a second business path; Stage 3 removes social attention heuristics rather than maintaining both policies.

## Consequences

- Social working state survives restart and remains isolated per scene.
- Event, operational scene state, and social session state cannot partially commit.
- Multi-person exchanges reach cognition as one ordered unit rather than independent sender batches.
- Runtime records observable facts but does not interpret the social state fields.
- Session event references grow until the explicit V4 context rollover work in Stage 4; Stage 1 does not discard raw context pre-emptively.
- The LLM still has proposal authority only. `RuntimeGate`, action serialization, freshness, cancellation, scheduler claims, permissions, and scope enforcement are unchanged.
