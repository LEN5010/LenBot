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
│   ├── models.py        # EpisodeOutcome, MessageProposal, TaskProposal, RetainedItemProposal
│   ├── pi_core.py       # PiAgentCore (ReAct loop with step-boundary steering)
│   ├── providers.py     # ProviderRegistry (multi-provider config & tier routing)
│   └── router.py        # CognitionRouter (Normal <-> Deliberate escalation)
├── events/              # Immutable event bus & raw storage
│   ├── builder.py       # StimulusBuilder (burst coalescing & debounce)
│   ├── models.py        # Event, EventType, Stimulus, StimulusType
│   └── store.py         # EventStore (SQLite WAL + native trigram FTS5 + traces)
├── memory/              # Epistemic beliefs & reflection
│   ├── gate.py          # MemoryGate (provenance check & slot superseding)
│   ├── models.py        # EpisodeRecord (L1), MemoryItem (L2), MemoryKind, MemoryCertainty
│   ├── reflector.py     # LLMReflector (event range -> EpisodeRecord + MemoryProposals)
│   ├── reflection.py    # ReflectionEngine (quiet-window micro-reflection & episode archival)
│   └── store.py         # MemoryStore (episodes, memories, reflection cursors)
├── runtime/             # Core persistent runtime
│   ├── agent_runtime.py # AgentRuntime coordinator
│   ├── gate.py          # RuntimeGate (staleness gate, two-phase commit)
│   └── metrics.py       # RuntimeMetrics (routing + social behavior counters)
├── scenes/              # Scene state management
│   ├── actor.py         # SceneActor (single-writer asynchronous worker)
│   ├── manager.py       # SceneManager (actor registry)
│   ├── models.py        # SceneState, ParticipationThread
│   └── reducer.py       # SceneReducer (pure functional state transition)
├── scheduler/           # Deterministic time execution
│   ├── engine.py        # TaskScheduler (min-heap + condition-bound wake + sync)
│   └── models.py        # TaskItem (due_at, wake_event_type), TaskStatus
├── state/               # Social and operational state
│   ├── ambient.py       # AmbientStore (short-lived retained soft state, in-memory)
│   ├── interest.py      # InterestModel (topic weights & scoring)
│   └── open_loops.py    # OpenLoopManager (TTL sweeper & decay)
├── testing/             # Deterministic test utilities
│   ├── replay.py        # ReplayLab (deterministic offline attention/cognition replay)
│   └── scenario_runner.py # ScenarioRunner for offline behavioral replay
├── plugins/             # Plugin runtime (ADR-0016/0021)
│   ├── builtin/         # Real plugins: bilibili_live sensor, web_search tool
│   ├── base.py          # BasePlugin, PluginContext (permission-guarded)
│   ├── host.py          # PluginHost (sandbox, lifecycle health, enable/disable)
│   └── models.py        # PluginManifest (config_schema, emitted_events, ...)
├── web/                 # Control Plane (ADR-0017/0022)
│   ├── frontend/        # Vue 3 + Vite source (builds to web/static/dist)
│   ├── routes/          # auth, overview, cockpit, models, plugins, replay, ...
│   ├── query_service.py # RuntimeQueryService — the ONLY read facade for routes
│   ├── log_ring.py      # In-memory operational log ring
│   └── app.py           # FastAPI app factory (SPA + API)
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

### Atomic All-or-Nothing Proposal Commit (ADR-0013)
When an episode finishes with multiple proposals (`tasks`, `resolve_open_loop_ids`, `memory_proposals`):
- `RuntimeGate` delegates to `EventStore.commit_proposal_transaction`.
- All durable internal state mutations and evidence integrity checks are executed inside a single SQLite transaction under `_write_lock`.
- If any mutation or validation fails, everything is rolled back: zero partial writes leak into SQLite.
- External side-effects (`TaskScheduler` heap registration and `ActionQueue` enqueue) only execute after database transaction commits successfully.

### Social Behavior Core & Interim Context (ADR-0014)
Conversational continuity in multi-user groups is governed by `ParticipationThread`:
- Natural continuation without `@` is permitted when an active thread matches topic and participants, bounded by speaking budget and conversational adjacency.
- When topic drifts, cognition emits `state_annotations={"close_thread": True}`, materialized via `STATE_ANNOTATION` event to step the bot out cleanly.
- `EpisodeMailbox` tracks unread interim events; `PiAgentCore` injects contemporary scene chatter at ReAct step boundaries so that cognition detects semantic staleness (e.g. peer answered) and outputs `SILENCE`.

### Semantic Memory & Scope Guard (ADR-0015)
Epistemic beliefs follow strict evidence-based provenance and semantic slot superseding:
- L2 beliefs store evidence event IDs; slot conflicts (`subject`, `kind`, `key`, `scope`) update previous records to `SUPERSEDED` pointing to `superseded_by` rather than erasing history.
- Privacy boundaries (`ExecutionScope`) are rigidly enforced at SQL layer (`WHERE (scope IN (...) OR visibility = 'global')`); private chat secrets can never be retrieved or leaked into public groups.
- Exponential temporal decay sweeper discounts stale unreinforced tentative beliefs while access frequency reinforces enduring knowledge.

### Plugin Runtime Isolation & Sensory Decoupling (ADR-0016)
Plugin capabilities are managed via `PluginHost` sandboxing:
- Plugins operate as sensory inputs emitting events into the runtime event bus; they never directly prompt LLMs or send outbound messages (Goal 6, Invariant B).
- Plugin tools are executed with timeout protection (`asyncio.wait_for`) and comprehensive exception trapping; crashing or hanging plugins cannot stall the runtime or leak uncaught exceptions to `PiAgentCore` (Goal 7).
- Outbound action interceptors in `ActionQueue` enable pre-flight content safety sanitization and action blocking.

### Operational Cockpit & Zero-Downtime Hot Reload (ADR-0017)
Real-time administrative control is centralized in the Cockpit API (`/api/cockpit/`):
- Observability exposes scene participant statistics, active participation threads, scheduled tasks, open loops, and epistemic memories.
- Safe human intervention (event injection, task cancellation/trigger, loop resolution, memory refutation) strictly honors the `Event -> Runtime State` invariant without bypass.
- Dynamic configuration updates (models, persona identity, speaking budget) hot-reload in-memory across all runtime components and persist in SQLite `configs` table across restarts (Goal 8 & 9).

### Condition-Bound Obligations & Ambient Items (ADR-0018)
Cross-time social continuity for promises and retained interests:
- `TaskItem.wake_event_type` turns a TaskProposal into a condition-bound obligation: it fires via the standard `TASK_DUE` authority path when a matching event commits in its scene, or at its deadline — whichever comes first. "开播叫我" + `LIVE_STARTED` → WAKE → fulfil.
- Plugin fact events (`LIVE_STARTED/LIVE_ENDED` → `PLUGIN_FACT` stimulus) are explicitly OBSERVE-only in `AttentionEngine`: a bare fact never wakes cognition and never creates a task (Invariant F & H).
- `AmbientStore` (in-memory, TTL ~hours) keeps short-lived retained soft state. Cognition may retain via `EpisodeOutcome.retained_item_proposals` (written post-transaction, never inside it); relevant items enter the Situation Package as 【RELEVANT AMBIENT ITEMS】 and cognition decides to use them or SILENCE.
- `OpenLoopManager.resolve_loop` only transitions ACTIVE loops and preserves target/intent/source for traceability.

### Quiet-Window Reflection & Typed Social Memory (ADR-0019)
- Reflection fires after a scene stays quiet for `reflection_quiet_window_seconds` (debounce timer per scene) — the old message-count trigger is gone. While `bot_engagement == "active"` the window postpones.
- A per-scene `reflection_cursors` row (last event rowid) bounds each reflection to the unreflected range (`EventStore.get_events_since`); the cursor advances only after the episode record persists — the same events are never summarized twice.
- The LLM reflector (`memory/reflector.py`) is wired in production; it only PROPOSES `EpisodeRecord + MemoryProposal[]`, with evidence citing the reflected range (Invariant E). Deterministic fallback remains only for explicit mock/test modes.
- `MemoryKind` is a canonical enum (preference/habit/relationship/fact/group_norm/topic_interest/recurring_role/social_pattern); legacy `pattern` rows migrate once at startup. Sender snapshots flow into a 【CURRENT ACTOR】 person card (display name, group role, subject-scoped memories). `decay_memories` runs in the maintenance heartbeat.

### Provider Registry & Routing Metrics (ADR-0020)
- `ProviderRegistry` is the single authority for tier → (provider, model, client): multi-provider OpenAI-compatible configs, hot `apply_update`, lazily cached clients, persisted in `provider_config` (one-time migration from legacy `model_config`).
- `RuntimeMetrics` records every live LLM call per (tier, provider, model): calls, errors, prompt/completion tokens, latency p50/p95, escalation reasons — plus social counters (human_messages, observe/track/wake, wake_silence, visible_messages, visible_speech_ratio, would_send, cancellations_honored).
- `PiAgentCore` resolves per step, so tier switches and hot config updates apply at the next ReAct step; escalation (`should_escalate`) returns a reason that lands in metrics. Retrieval tools with large evidence sets append the `[COMPLEXITY: HIGH]` marker — the escalation signal has real producers.

### Plugin Discovery, Lifecycle Health & Real Plugins (ADR-0021)
- `PluginManifest` declaratively exposes `config_schema/default_config/emitted_events/registered_tools`; `PluginHost` tracks per-plugin health (state, last_error, error_count, last_event_at, last_run_at) and `on_enable/on_disable` hooks are actually invoked.
- Discovery is an explicit builtin registry (`plugins/builtin/`); `AgentRuntime.start()` loads builtin plugins with persisted config + enabled flags (`plugins_state`), `stop()` unloads. `save_plugin_state()` is the persistence authority.
- Real plugins: `BilibiliLiveSensor` (polls the public live API, emits `LIVE_STARTED/LIVE_ENDED` facts, inert when unconfigured, never notifies directly) and `WebSearchToolPlugin` (`web_search` + `read_page` tools over DuckDuckGo, sandboxed, failures return error strings). The mock `RESERVED_PLUGINS` registry was deleted — the Control Plane shows the real registry only.

### Control Plane Query Service, Trace & Replay Lab (ADR-0022)
- `RuntimeQueryService` is the only read facade for web routes — no route touches `runtime.*._db`, `_actors` or `_plugins`; interventions keep their authority paths (loop resolve goes through `OpenLoopManager`).
- `traces` table stores per-stimulus attention rows and per-episode chain rows (Stimulus → Attention → per-step cognition → Outcome → Gate → durable effects → actions): the "why did the bot speak/stay silent" question is answerable from the Trace view.
- Metrics/social endpoints, filtered event queries, and an in-memory log ring (`GET /api/logs`) complete observability. `ReplayLab` deterministically replays a recorded window through the pure reducer + real AttentionEngine with policy override sets (Replay Lab, Policy A vs B).
- The Vue 3 + Vite frontend (`web/frontend/`, builds to `web/static/dist/`) implements the ten-view information architecture; CORS wildcard+credentials was removed.

### Shadow Mode (ADR-0023)
- `AgentRuntime.set_shadow_mode()` hot-toggles (persisted in `shadow_config`). While enabled, `ActionQueue` still runs safety interceptors but skips physical sends and emits NO `MESSAGE_SENT` — zero social facts, no OpenLoop activation — while recording would-send entries (`shadow_would_send_log`, `metrics.would_send`).
- Use for the Shadow Testing phase on real groups: observe false-positive speaking, stale responses and awkward participation before enabling real delivery.

### Strict Scope Isolation and Safe Promotion (ADR-0024)
- `MemoryItem.visibility` is completely removed; `scope` is the sole boundary authority.
- Cognition and reflection only read and write to the current `scene_id`.
- Only human operators via the Control Plane can promote an active memory to `global-safe` (`POST /api/cockpit/memories/{id}/promote`), creating an immutable new record without erasing the original.
- The cognitive proposal model replaced `thought` with typed `decision_reason`.

### Proposal Contract and Social State Authority (ADR-0025)
- Added `ThreadTransition(KEEP, FADE, CLOSE)` and `SocialStateProposal(topic, thread_transition)`.
- `GateDecision` has explicit `accepted: bool`; `STATE_ANNOTATION` events are only emitted when `decision.accepted is True`.
- `validate_memory_proposal` enforces strict evidence validation, non-empty keys, and canonical `MemoryKind` enums.
- Open loops resolve under atomic `WHERE id=? AND scene_id=? AND status='active'` guarantees.

### Semantic Staleness Gate & Interim Filtering (ADR-0026)
- `EpisodeMailbox` only accepts human chat events (`GROUP_MESSAGE_RECEIVED`, `PRIVATE_MESSAGE_RECEIVED`), filtering out internal state and sensor facts.
- Multi-step cognition checks `mailbox.has_unseen_interim()` at step boundaries; if interim chatter arrives during the final step, the outcome fails closed to `SILENCE`.
- `RuntimeGate` rejects stale outcomes where interim human events were not incorporated, tracking `stale_outcomes_rejected`.

### Participation Lifecycle & Attention Continuation (ADR-0027)
- `ParticipationThread` implements a 2-minute fade (`THREAD_FADE_AFTER = 120.0s`) and 5-minute close (`THREAD_CLOSE_AFTER = 300.0s`).
- `last_relevant_at` only updates on relevant mentions, replies, or active topic continuations; intervening chatter increments message counter without prolonging the decay clock.
- Attention continuation requires topic match or immediate adjacency; off-topic participant chatter evaluates to `OBSERVE` with reason `participant_off_topic`.

### Quiet-Window Reflection & Atomic Batch Commit (ADR-0028)
- Unreflected events are fetched via `get_unreflected_events(after_rowid, limit=30)` without skipping older events.
- `commit_reflection_batch` executes episode insertion, proposal validation, and cursor advance in a single atomic SQLite transaction; cursor never advances on failure.
- Keyless mode defaults to deterministic heuristic reflection without crashing when live LLM provider is unconfigured.

### Condition-Bound Wake Match & Durable Task Claim (ADR-0029)
- `TaskProposal` supports `wake_match` exact key-value evaluation and origin tracking (`origin_episode_id`, `origin_stimulus_id`).
- Task scheduling and event-driven wake claim use `UPDATE tasks SET status='claimed', trigger_event_id=? WHERE id=? AND status='pending'` ensuring exactly-once execution.
- Added `promote_task` API to release event conditions and allow immediate scheduled execution.

### Plugin Lifecycle, Tool Namespace & SSRF Policy (ADR-0030)
- `RESERVED_CORE_TOOLS` prevents plugins from registering tools that shadow built-in retrieval capabilities.
- Dynamic plugin lifecycle correctly starts and cancels background polling tasks on `on_enable` and `on_disable`.
- Centralized SSRF network policy blocks loopback, RFC 1918, RFC 3927 cloud metadata (`169.254.169.254`), and internal domains without breaking macOS/Linux transparent proxy tunnels.

### Social Hot Reload, Shadow Annotations & OneBot Hardening (ADR-0031)
- `POST /api/cockpit/social` immediately synchronizes `AttentionEngine.monitored_keywords` in memory.
- `shadow_annotations` table and endpoints (`POST /api/cockpit/shadow-annotations`, `GET /api/cockpit/shadow-annotations`) track TP/FP/TN/FN human evaluation feedback and accuracy metrics.
- OneBot v11 adapter drops bot's own self-sent echo messages, parses `reply_to_message_id` into a 1000-item ring buffer, and formats quote replies with `[CQ:reply,id=...]`.

---

## 4. Development & Testing Workflow

This project uses `uv` for lightning-fast Python package and environment management.

### Running Tests
```bash
# Run the entire test suite
uv run pytest

# Run the V2 Scenario Benchmark (Goals 1-12, named A-L)
uv run pytest tests/test_v2_scenarios_a_to_l.py -v

# Run specific test modules with verbose output
uv run pytest tests/test_v1a_reactive_core.py -v
uv run pytest tests/test_v1b_persistent_execution.py -v
uv run pytest tests/test_v1c_agentic_history.py -v
uv run pytest tests/test_v1d_memory.py -v
uv run pytest tests/test_v1e_agency.py -v
```

### Control Plane Frontend
```bash
# The Vue 3 control plane builds into web/static/dist (served by FastAPI)
cd src/len_bot/web/frontend && npm install && npm run build

# Dev mode: vite dev server proxies /api to the running control plane
cd src/len_bot/web/frontend && npm run dev
```

### Adding New Architectural Decisions (ADR)
Whenever proposing a fundamental change to state ownership, database schemas, concurrency, or execution gating:
1. Create a markdown file under `docs/adr/NNNN-<short-name>.md`.
2. Document **Context**, **Decision**, and **Consequences**.
3. Update `CONTEXT.md` with any new domain terms.
