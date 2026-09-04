# Persistent Social Agent Runtime - Ubiquitous Domain Language (CONTEXT.md)

A persistent runtime environment for autonomous social agents that maintains temporal continuity, social relationships, and execution state across scenes, treating LLMs strictly as ephemeral cognitive executors.

---

## 1. World & Observation

**Event**:
An immutable, historical record of a factual occurrence in the external world or within the runtime (e.g., `GROUP_MESSAGE_RECEIVED`, `TASK_DUE`, `MESSAGE_SENT`).
_Avoid_: Message, update, trigger

**Conversation Burst**:
An ordered group of committed events from one scene, assembled only by short arrival timing and retaining every source event reference.
_Avoid_: Request, per-user batch, attention trigger

**Burst Assembler**:
A lightweight temporal assembler that creates conversation bursts without deciding topic, interest, social relevance, or whether cognition should run.
_Avoid_: Attention engine, classifier, semantic batcher

**Historical Ingestion Gate**:
An ingress filter that admits delayed or reconnected historical events into the Event Store while suppressing stimulus generation to prevent attention flooding.
_Avoid_: Message filter, deduplicator

**OneBot Link**:
The single runtime-owned bridge that receives OneBot events over WebSocket and delivers approved actions through the operator-selected OneBot transport.
_Avoid_: Plugin connection, message sender, protocol session

**OneBot Connection Mode**:
Whether LenBot actively connects to a OneBot WebSocket or listens for a OneBot client to connect; both modes feed the same event normalization path.
_Avoid_: Bot mode, server role, deployment type

**OneBot Action Transport**:
The explicitly selected protocol used for each approved outbound action (`websocket` or `http`), with no automatic cross-transport retry after an ambiguous send.
_Avoid_: Fallback channel, redundant sender

**Scene**:
An isolated conversational or observational space (such as a group chat, private chat, or livestream monitor) possessing its own independent context, ordering, and social dynamics.
_Avoid_: Channel, room, session, thread

**Scene Actor**:
A dedicated asynchronous worker coroutine consuming an isolated event queue for a single scene to serialize state mutations and route steering signals.
_Avoid_: Event loop, thread, listener

**Group Agent Session**:
The durable working social state of the agent in one scene, restored across runtime restarts and never hidden solely inside model context.
_Avoid_: LLM session, HTTP session, global conversation state

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

**Retrieval Tool Loop**:
The bounded ReAct loop inside Social Cognition Core through which the model may call history and memory retrieval tools on demand before producing its structured decision.
_Avoid_: Auto-RAG, per-message memory injection, agent framework

**Fast Social Cognition**:
The one-shot low-latency cognition call for casual social bursts that decides silence, speaks final short messages, or escalates to full cognition — nothing else.
_Avoid_: Classifier, attention filter, cheap model substitute for judgement

**Full Cognition**:
The complete Social Cognition Core episode with agentic retrieval, world-model rebuild, and typed proposals, used for complex, tool-bound, or escalated bursts.
_Avoid_: Slow mode, retry path, optional luxury

**Tool Budget**:
The deterministic per-episode ceiling on retrieval tool executions (`max_tool_calls`); exhausting it, or reaching the last loop step, forces a final decision via `tool_choice="none"`.
_Avoid_: Soft suggestion, retry limit

**Forced Final**:
A protocol enforcement step in which the retrieval tool loop demands a speak/silence decision from the model instead of allowing further tool calls.
_Avoid_: Timeout, silent abort

**Model Fallback**:
The single-hop secondary route target attempted once when the primary provider fails; never chained and never used when identical to the primary.
_Avoid_: Retry, failover cluster, provider chain

**Model Catalog**:
The provider-specific, operator-curated list of advertised models available for tier and fallback selection.
_Avoid_: Global model registry, model marketplace

**Social Core Context**:
The direct cognitive context composed from core self, group identity, current social and self state, recent raw conversation, unresolved social threads, and the current burst.
_Avoid_: Generic top-k RAG, prompt history as state

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

**Social World State**:
The agent's revisable understanding of current topics, social dynamics, open social threads, mood, and latent expectations in one scene.
_Avoid_: Runtime rule flags, keyword topic map

**Self Social State**:
The agent's revisable understanding of its own recent participation, social position, interest, speaking inclination, and received feedback in one scene.
_Avoid_: Speaking score, single budget number

**Working Person Model**:
Current scene-local knowledge about a participant needed for ongoing conversation, grounded in recent observations and durable memory.
_Avoid_: User profile row, global identity record

**Relationship Model**:
Current scene-local knowledge of the agent's interaction history and communication patterns with a participant.
_Avoid_: Person model, affinity score

**Latent Expectation**:
A non-obligatory expectation that a future condition may make an unresolved social matter worth reconsidering.
_Avoid_: Task, timer, automatic notification

**Next Wake Intent**:
A cognition proposal to observe a scene again at a future time; the scheduler and runtime gate retain all authority to validate and commit it.
_Avoid_: Model-owned timer, scheduled message

**Pending Next Wake**:
The single scene-scoped durable task created from an accepted Next Wake Intent; its committed due time and origin mode are the scheduling truth shown to cognition.
_Avoid_: Session timer, model wake state, retained attention

**Intentional Silence**:
A successful cognition result in which the agent understood the event and chose not to participate.
_Avoid_: Dropped event, ignored input, model failure

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

**Identity Core**:
The stable, long-term layer of the persona expressed as observable behavioural tendencies (how it treats friends versus strangers, when it is serious or dismissive, its humour and conflict style).
_Avoid_: Static prompt string, adjective list, mood simulator

**Group Register**:
Per-scene factual statistics of how the group actually chats (message length, fragmentation, punctuation, emoji, questions, common short reactions), rendered as style context only and never consulted by decisions.
_Avoid_: Style rule engine, accommodation enforcer, vocabulary copier

**Voice Exemplar**:
A curated record of the bot's own past expression used as rotating few-shot style material; selection is least-recently-used per scene and never copied verbatim into replies.
_Avoid_: Fixed few-shot block, template reply, training data claim

**Immediate State**:
The strictly consistent state a FAST result commits in the current event chain: observation cursor, bot's own message facts, open loops, consecutive-message counters, staleness, and gate authority.
_Avoid_: Cached reply state, lazy write, eventually-consistent side effects

**Deferred Cognition**:
Long-horizon social understanding (group mood, topic evolution, identity) proposed by quiet-window reflection as a merge-only patch applied through the lawful event path after the fact.
_Avoid_: Second cognition agent, blocking pre-send work, wholesale world rewrite

**Style Guard**:
A purely local anti-slop detector over the bot's recent messages (duplicates, repeated openers, repeated n-grams) that feeds metrics and permits at most one corrective retry.
_Avoid_: LLM critic, output censorship, sampler-only fix

---

## 5. Proactive Agency & Safety

**Hard Speaking Ceiling**:
A deterministic anti-loop and spam safety limit that rejects extreme output patterns without deciding normal social timing.
_Avoid_: Social judgement, participation heuristic

**Monologue Prevention**:
A hard budget barrier that forbids unsolicited proactive initiative when the bot has already sent two or more consecutive messages without human intervention.
_Avoid_: Spam check, flood gate

**Retained Attention**:
A scene-local cognitive note that something remains interesting without creating a task or requiring an immediate visible action.
_Avoid_: Reminder, pending reply, hidden task

**Projected Social Context**:
A model-facing view of immutable OneBot events that replaces transport-only CQ payloads with compact semantic markers and rolls out the oldest raw messages only when the configured token budget is reached.
_Avoid_: Raw-history rewrite, summary-only context, fixed message-count window

**Direct Cognition Preemption**:
Deterministic cancellation of an in-flight model call when a newer direct mention or reply in the same scene makes its result necessarily stale; the SceneActor, Gate, and action commit paths are never interrupted.
_Avoid_: Attention heuristic, priority classifier, bypass response

---

## 6. Testing & Verification

**Scenario Runner**:
An offline test execution harness that feeds timestamped event traces into the runtime to assert deterministic state transitions, cognitive decisions, and gate outcomes.
_Avoid_: Mock framework, integration test

**Social Continuity Error**:
A behavioral failure where the agent loses or misapplies scene context, speaker relationships, topic/thread identity, its own recent behavior, or conversational timing.
_Avoid_: Wrong answer only, generic hallucination rate
