# AGENTS.md - Developer & Agent Engineering Guide

> **Persistent Social Agent Bot Harness (len_bot)**  
> *A long-running, socially persistent agent runtime with temporal awareness, episodic memory, and deterministic execution gating.*

---

## 1. Core Philosophy & Invariant Constraints

This codebase is **not** an LLM wrapper or a traditional chatbot framework. The persistent runtime **is** the Agent; large language models (LLMs) are merely ephemeral, on-demand cognitive processors invoked during bounded **Cognitive Episodes**.

### The 10 Invariant Architectural Constraints (§109)

When modifying or adding features to this codebase, you **must strictly adhere** to these non-negotiable rules:

1. **No bypassing `Event → Runtime State`**: All state modifications (`SceneState`, tasks, open loops, memories) must originate from an `Event` or an approved proposal producing events.
2. **No direct prompt-and-send by plugins**: Plugins and adapters are sensory inputs only; they emit events into the bus and never trigger inference or send messages directly.
3. **No persistent LLM sessions**: Never store state inside LLM prompt history or permanent chat sessions. All truth is materialized in SQLite WAL tables.
4. **No execution authority in Reflection**: Reflection processes only propose epistemic beliefs (`MemoryProposal`); they have zero authority over tasks, open loops, or action queues.
5. **No implicit task creation from soft state**: Soft annotations (e.g. `possible_start_time`) must never automatically spawn scheduled tasks. Tasks require explicit `TaskProposal` from cognition.
6. **No direct side-effects from Pi**: Pi outputs structured `EpisodeOutcome` containing proposals. Actions must pass through `RuntimeGate` validation.
7. **No overwriting raw history with memory**: The `events` table is append-only and immutable. Memories are subjective epistemic beliefs with evidence pointers back to raw events.
8. **No automatic RAG on every message**: Prompts include only a tight elastic window of recent raw messages. Historical context is retrieved actively and on-demand via tools (`search_messages`, `read_context`, `query_timeline`).
9. **No prompt-level privacy enforcement**: Privacy boundaries (`ExecutionScope`) must be rigidly enforced at the SQL layer (`WHERE scene_id IN (...)`), never by asking the model not to disclose private information.
10. **No randomness as primary agency**: Proactive behavior is governed by an explicit `InterestModel` evaluated against a dynamic `SpeakingBudget` cost curve.

---

## 2. Directory Structure & Module Boundaries

```text
src/len_bot/
├── actions/             # Side-effect management
│   ├── models.py        # ActionItem, ActionType
│   └── queue.py         # ActionQueue (Two-Phase Commit for Open Loops)
├── adapters/            # External sensory and protocol adapters
│   └── onebot.py        # OneBot v11 Reverse WebSocket adapter
├── attention/           # Attention filtering & initiative
│   ├── budget.py        # SpeakingBudget (anti-chatterbox thresholding)
│   ├── engine.py        # AttentionEngine (Hard, Heuristic, Initiative)
│   ├── initiative.py    # InitiativeEngine (WAKE_FOR_INITIATIVE, RETAIN_FOR_LATER)
│   └── models.py        # AttentionResult, AttentionDisposition
├── cognition/           # Ephemeral cognitive execution
│   ├── assembler.py     # ContextAssembler (Situation Package generator)
│   ├── mailbox.py       # EpisodeMailbox (in-flight steering & cancellation)
│   ├── manager.py       # EpisodeManager (ephemeral episode lifecycle)
│   ├── models.py        # EpisodeOutcome, MessageProposal, TaskProposal
│   ├── pi_core.py       # PiAgentCore (ReAct loop with step-boundary steering)
│   └── router.py        # CognitionRouter (Normal <-> Deliberate escalation)
├── events/              # Immutable event bus & raw storage
│   ├── builder.py       # StimulusBuilder (burst coalescing & debounce)
│   ├── models.py        # Event, EventType, Stimulus, StimulusType
│   └── store.py         # EventStore (SQLite WAL + native trigram FTS5)
├── memory/              # Epistemic beliefs & reflection
│   ├── gate.py          # MemoryGate (provenance check & slot superseding)
│   ├── models.py        # EpisodeRecord (L1), MemoryItem (L2), MemoryCertainty
│   ├── reflection.py    # ReflectionEngine (micro-reflection & episode archival)
│   └── store.py         # MemoryStore (episodes & memories tables)
├── runtime/             # Core persistent runtime
│   ├── agent_runtime.py # AgentRuntime coordinator
│   └── gate.py          # RuntimeGate (staleness gate, two-phase commit)
├── scenes/              # Scene state management
│   ├── actor.py         # SceneActor (single-writer asynchronous worker)
│   ├── manager.py       # SceneManager (actor registry)
│   ├── models.py        # SceneState, ParticipantStats
│   └── reducer.py       # SceneReducer (pure functional state transition)
├── scheduler/           # Deterministic time execution
│   ├── engine.py        # TaskScheduler (min-heap + asyncio.Event + sync)
│   └── models.py        # TaskItem, TaskStatus
├── state/               # Social and operational state
│   ├── interest.py      # InterestModel (topic weights & scoring)
│   └── open_loops.py    # OpenLoopManager (TTL sweeper & decay)
├── testing/             # Deterministic test utilities
│   └── scenario_runner.py # ScenarioRunner for offline behavioral replay
└── tools/               # Agentic retrieval tools
    └── retrieval.py     # RetrievalToolkit (search_messages, read_context, etc.)
```

---

## 3. Key Architectural Patterns

### Two-Phase Proposal Commit (ADR-0003)
When an episode proposes a message that expects a reply (`expect_reply=True`, `reply_target="user:123"`):
- `RuntimeGate` attaches an `associated_open_loop` to the `ActionItem`.
- `ActionQueue` attempts physical transmission via the network adapter.
- Only when `MESSAGE_SENT` is confirmed is the `OpenLoop` activated and written to SQLite.
- If transmission fails, `MESSAGE_SEND_FAILED` is emitted and the loop is discarded.

### Single-Writer Scene Actor (ADR-0004)
Every scene (group chat or private chat) has a dedicated `SceneActor` coroutine consuming an isolated `asyncio.Queue[Event]`.
- State transitions are executed synchronously by `SceneReducer.reduce`.
- The Scene Actor **never blocks** for LLM inference.
- An in-flight episode attaches an `EpisodeMailbox` to the actor to receive steering events.

### Step-Boundary Steering (ADR-0002)
During a multi-step ReAct tool loop in `PiAgentCore`:
- At each step boundary (before model call and after tool execution), `mailbox.check_steering()` is invoked.
- If a user sends "算了/不用了/别查了", a `SteeringType.CANCEL` signal is returned.
- Cognition aborts early and returns `FinalDisposition.SILENCE`.

### Ambient ExecutionScope (ADR-0006)
All retrieval tools (`search_messages`, `read_context`, `query_timeline`, `query_person_history`, `query_memory`) take `allowed_scopes: list[str]`.
- SQL queries unconditionally enforce `WHERE scene_id IN ({placeholders})`.
- The LLM cannot access private conversations or other groups, regardless of prompt injections.

---

## 4. Development & Testing Workflow

This project uses `uv` for lightning-fast Python package and environment management.

### Running Tests
```bash
# Run the entire test suite
uv run pytest

# Run specific test modules with verbose output
uv run pytest tests/test_v1a_reactive_core.py -v
uv run pytest tests/test_v1b_persistent_execution.py -v
uv run pytest tests/test_v1c_agentic_history.py -v
uv run pytest tests/test_v1d_memory.py -v
uv run pytest tests/test_v1e_agency.py -v
```

### Adding New Architectural Decisions (ADR)
Whenever proposing a fundamental change to state ownership, database schemas, concurrency, or execution gating:
1. Create a markdown file under `docs/adr/NNNN-<short-name>.md`.
2. Document **Context**, **Decision**, and **Consequences**.
3. Update `CONTEXT.md` with any new domain terms.
