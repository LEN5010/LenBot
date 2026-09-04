# ADR-0033: Social Cognition Core Shadow Contract

## Status

Superseded in part by ADR-0034 — 2026-09-04

## Context

Before V4 can replace social attention heuristics, the new cognition contract must be exercised against real scene context without acquiring outbound authority. The evaluation must distinguish an event that was ignored before cognition from one the agent understood and intentionally answered with silence.

## Decision

Introduce one `SocialCognitionCore` using the existing provider registry and the same stable core persona on every call. Its direct context contains `GroupAgentSession`, up to 80 recent raw conversation events, the current ordered burst, and active open loops. It does not receive generic top-k memory injection.

The strict `SocialCognitionResult` contains:

- a perception summary and complete proposed `SocialWorldState` snapshot;
- scoped person and relationship updates with event evidence;
- a subjective `SelfSocialState` update;
- exactly one `speak` or `silence` decision and its reason;
- message proposals, retained attention, future attention, and memory candidates.

The schema forbids unknown fields. `silence` cannot carry a message proposal, and `speak` requires one. This stage does not execute tools, schedule future attention, commit memory candidates, or send proposed messages.

When existing Shadow Mode is active and a model or explicit test handler is configured, each burst also enters the Social Core. This is the Stage 2 migration probe, not a permanent policy switch: the V3 path continues to provide its existing would-send baseline until Stage 3 deletes social Attention and makes Social Core the sole cognition path.

Social inference runs outside `SceneActor`. The actor validates that its observation cursor has not advanced and that all evidence IDs belong to the scene session. If valid, it atomically commits a `SOCIAL_COGNITION_RECORDED` event, `SceneState`, and the updated `GroupAgentSession`. If a newer event arrived, the result is rejected and the per-scene shadow loop reruns against the newest state. The LLM never writes session state directly.

## Consequences

- Shadow traces expose intentional silence, proposed speech, perception, and social state changes.
- Shadow cognition has zero path to `ActionQueue` or scheduler authority in this stage.
- Social context persists across successive shadow cognitions and restarts.
- Per-scene coalescing prevents concurrent Social Core calls from racing to write the same session.
- The Stage 2 V3/V4 comparison is intentionally temporary and must be removed by Stage 3 rather than protected by a new feature flag.
