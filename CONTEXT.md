# Persistent Social Agent Runtime - Ubiquitous Domain Language (CONTEXT.md)

A persistent runtime environment for autonomous social agents that maintains temporal continuity, social relationships, and execution state across scenes, treating LLMs strictly as ephemeral cognitive executors.

---

## 1. World & Observation

**Event**:
An immutable, historical record of a factual occurrence in the external world or within the runtime (e.g., `GROUP_MESSAGE_RECEIVED`, `TASK_DUE`, `MESSAGE_SENT`).
_Avoid_: Message, update, trigger

**Stimulus**:
A structured cognitive trigger synthesized by debouncing or coalescing one or more raw events, prepared for attention evaluation.
_Avoid_: Raw event, input message, alert

**Historical Ingestion Gate**:
An ingress filter that admits delayed or reconnected historical events into the Event Store while suppressing stimulus generation to prevent attention flooding.
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

---

## 2. Execution & Cognition

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

**Cognitive Tier**:
The operational capability level of the LLM invoked during an episode (`NORMAL` for everyday banter and standard tools, `DELIBERATE` for deep multi-step reasoning or high-complexity tool outputs).
_Avoid_: Model size, prompt mode

**Cognition Router**:
The runtime orchestrator that selects the appropriate model tier and executes dynamic in-flight escalation based on tool result complexity or step depth.
_Avoid_: Model switcher, prompt dispatcher

**Elastic Context Partition**:
A prioritized prompt budgeting policy where core identity, relevant memory beliefs, and execution state are strictly preserved while raw event history shrinks elastically under token limits.
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
A transactional gate mechanism where internal state proposals commit immediately, while interaction-dependent states (such as open loops expecting replies) commit only upon external action confirmation (`MESSAGE_SENT`).
_Avoid_: Distributed commit, sync save

---

## 3. Social & Temporal State

**Open Loop**:
An explicit, tracked social or task dependency that has been initiated but not yet concluded (e.g. waiting for user A to answer an inquiry).
_Avoid_: Pending message, callback, promise

**Task**:
An explicit future execution intent with a defined target and trigger condition, managed deterministically by the runtime.
_Avoid_: Reminder, cron job, delayed message

**Task Scheduler**:
A deterministic, priority min-heap time execution engine that wakes on scheduled deadlines to inject `TASK_DUE` events into the event bus without calling language models.
_Avoid_: Cron daemon, timer loop

**Execution Scope**:
An authoritative security and privacy boundary injected by the runtime that rigidly restricts database and retrieval queries to permitted scenes and global items at the SQL layer.
_Avoid_: Access control list, user role, filter param

**Materialized State Table**:
An authoritative, directly queryable database record reflecting the current state of runtime entities (scenes, tasks, open loops, memories), updated transactionally with events.
_Avoid_: Cache, read model, in-memory store

**Trigram FTS5 Index**:
A native SQLite full-text search index tokenizing character triples without external segmentation dependencies, used for agentic history retrieval across CJK text.
_Avoid_: Vector index, embedding store

---

## 4. Epistemic Memory & Reflection

**Memory**:
An epistemic belief about the world, persons, or relationships formed from past episodes, possessing explicit provenance and certainty levels.
_Avoid_: History, chat logs, vector embedding

**Episode Record (L1)**:
A structured experiential record of a past conversation block containing title, summary, participant list, semantic tags, and source event references.
_Avoid_: Chat summary, archive

**Memory Certainty**:
A qualitative assessment of an epistemic belief's reliability (`tentative`, `likely`, `strong`, `explicit`).
_Avoid_: Confidence float, probability score

**Memory Provenance**:
The verifiable causal audit chain linking every memory belief back to concrete source event IDs or episode IDs.
_Avoid_: Memory reference, citation

**Memory Gate**:
An authoritative gatekeeper that validates evidence provenance, verifies scope bounds, and resolves semantic slot collisions before committing memories.
_Avoid_: Memory updater, store writer

**Semantic Slot**:
A unique epistemic coordinate defined by `(subject, kind, key, scope)` representing a specific facet of an entity's state (e.g. user:1001 preference for hotpot).
_Avoid_: Key-value pair, property

**Memory Superseding**:
A conflict resolution policy where newly confirmed contradictory beliefs supersede older records by setting their status to `superseded` rather than physically overwriting or deleting historical truth.
_Avoid_: Delete, in-place update

**Micro-Reflection**:
A background cognitive consolidation job triggered after a conversation quiets down, transforming raw events into L1 episodes and proposing L2 social beliefs.
_Avoid_: Auto-summary, offline cleanup

---

## 5. Proactive Agency & Social Budget

**Speaking Budget**:
A dynamic social pressure regulator that calculates the cost of proactive speaking based on consecutive bot messages, recent speaking timestamps, and group traffic density.
_Avoid_: Rate limiter, throttle

**Monologue Prevention**:
A hard budget barrier that forbids unsolicited proactive initiative when the bot has already sent two or more consecutive messages without human intervention.
_Avoid_: Spam check, flood gate

**Interest Model**:
A declarative mapping of topic weights and keyword signals representing the agent's intrinsic curiosities, used to score incoming social observations.
_Avoid_: Embedding matcher, preference prompt

**Initiative Engine**:
The evaluation pipeline that compares an event's interest score against the scene's current speaking budget threshold to output `WAKE_FOR_INITIATIVE`, `RETAIN_FOR_LATER`, or `DISCARD`.
_Avoid_: Proactive timer, auto-chatter

---

## 6. Testing & Verification

**Scenario Runner**:
An offline test execution harness that feeds timestamped event traces into the runtime to assert deterministic state transitions, cognitive decisions, and gate outcomes.
_Avoid_: Mock framework, integration test

**Keyword Probe**:
A deterministic heuristic rule matching specific semantic interest tokens combined with speaking cooldown budgets to trigger candidate wake episodes.
_Avoid_: Regex trigger, word watcher
