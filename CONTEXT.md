# Persistent Social Agent Runtime

A persistent runtime environment for autonomous social agents that maintains temporal continuity, social relationships, and execution state across scenes, treating LLMs strictly as ephemeral cognitive executors.

## World & Observation

**Event**:
An immutable, historical record of a factual occurrence in the external world or within the runtime.
_Avoid_: Message, update, trigger

**Stimulus**:
A structured cognitive trigger synthesized by debouncing or coalescing one or more events, prepared for attention evaluation.
_Avoid_: Raw event, input message, alert

**Historical Ingestion Gate**:
An ingress filter that admits delayed or reconnected events into the Event Store while suppressing stimulus generation to prevent attention flooding.
_Avoid_: Message filter, deduplicator

**Scene**:
An isolated conversational or observational space (such as a group chat, private chat, or livestream monitor) possessing its own independent context, ordering, and social dynamics.
_Avoid_: Channel, room, session, thread

**Scene Actor**:
A dedicated asynchronous worker coroutine consuming an isolated event queue for a single scene to serialize state mutations and route steering signals.
_Avoid_: Event loop, thread, listener

**Sliding Idle Window**:
A debouncing buffer that coalesces consecutive message events from the same actor, resetting on each arrival up to an absolute time ceiling.
_Avoid_: Throttle, batch timer

## Execution & Cognition

**Persistent Runtime**:
The enduring agent authority that owns identity, execution state, time awareness, scheduling, and side effects.
_Avoid_: Bot framework, LLM wrapper, agent loop

**Cognitive Episode**:
A bounded, ephemeral reasoning process invoked by the runtime to interpret a stimulus and propose actions.
_Avoid_: Session, chat thread, conversation loop

**Episode Mailbox**:
An isolated asynchronous channel per running episode that receives contextual events while cognition is in flight.
_Avoid_: Event listener, input queue

**Steering**:
An in-flight redirection or cancellation signal injected into an active cognitive episode via its mailbox at step boundaries.
_Avoid_: Interrupt, abort signal, event override

**Cognition Escalation**:
A mid-episode promotion of the cognitive engine to a deeper reasoning model while preserving message trajectory, triggered by tool complexity or model self-request.
_Avoid_: Model restart, prompt swap

**Elastic Context Partition**:
A prioritized prompt budgeting policy where core identity and execution state are strictly preserved while raw event history shrinks elastically under token limits.
_Avoid_: Context truncation, token trimming

**Proposal**:
A candidate action, task, memory, or state modification produced by cognition, awaiting runtime validation before execution or commitment.
_Avoid_: Command, tool call execution, direct action

**Runtime Gate**:
The authoritative decision boundary that validates and either commits or rejects proposals before any real-world side effect or state mutation occurs.
_Avoid_: Guardrail, output filter, safety check

**Response Staleness Gate**:
A validation check within the Runtime Gate that evaluates whether interim scene events have invalidated an episode's proposed action.
_Avoid_: Delay check, timeout

**Two-Phase Proposal Commit**:
A transactional gate mechanism where internal state proposals commit immediately, while interaction-dependent states commit only upon external action confirmation.
_Avoid_: Distributed commit, sync save

## Social & Temporal State

**Open Loop**:
An explicit, tracked social or task dependency that has been initiated but not yet concluded.
_Avoid_: Pending message, callback, promise

**Task**:
An explicit future execution intent with a defined target and trigger condition, managed deterministically by the runtime.
_Avoid_: Reminder, cron job, delayed message

**Execution Scope**:
An authoritative security and privacy boundary injected by the runtime that rigidly restricts database and retrieval queries to permitted scenes and global items.
_Avoid_: Access control list, user role, filter param

**Materialized State Table**:
An authoritative, directly queryable database record reflecting the current state of runtime entities (scenes, tasks, open loops), updated transactionally with events.
_Avoid_: Cache, read model, in-memory store

**Trigram FTS5 Index**:
A native SQLite full-text search index tokenizing character triples without external segmentation dependencies, used for agentic history retrieval.
_Avoid_: Vector index, embedding store

**Memory**:
An epistemic belief about the world, persons, or relationships formed from past episodes, possessing provenance and explicit certainty.
_Avoid_: History, chat logs, vector embedding

## Testing & Verification

**Scenario Runner**:
An offline test execution harness that feeds timestamped event traces into the runtime to assert deterministic state transitions and gate outcomes.
_Avoid_: Mock framework, integration test

**Keyword Probe**:
A deterministic heuristic rule matching specific semantic interest tokens combined with speaking cooldown budgets to trigger candidate wake episodes.
_Avoid_: Regex trigger, word watcher
