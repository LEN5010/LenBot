# V1-A Reactive Core Scope with Stubs for Memory and Scheduler

> **Status — 2026-09-05:** Historical. The Attention module was removed in V4 Stage 3; `StimulusBuilder` was replaced by the per-scene `BurstAssembler` (ADR-0032); `EpisodeManager`/`ContextAssembler` were deleted; `PiCore` was renamed `ReActAgentCore` and is kept for boundary tests only (ADR-0035). Memory reflection and task scheduling are fully implemented (ADR-0019, ADR-0009).

To rapidly validate the foundational hypothesis—that an autonomous agent can deterministically decide when to speak versus remain silent without persistent LLM sessions—we decided that V1-A will deliver the complete reactive pipeline (OneBot, EventStore, SceneActor, StimulusBuilder, Attention, EpisodeManager, PiCore, RuntimeGate, and ActionQueue) while stubbing long-term memory reflection and complex background task scheduling. This isolates and proves core execution semantics before adding background cognitive jobs.
