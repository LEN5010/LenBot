# ADR-0039: Agent Jobs — Runtime-Owned Subagent Execution for Tool-Heavy Work

- Status: Proposed (draft — pending operator review; AGENTS.md/CONTEXT.md sync deferred until acceptance)
- Date: 2026-09-05
- Builds on: ADR-0035 (agentic retrieval loop), ADR-0038 §9.4 (deferred task proposals),
  ADR-0034 (anti-self-renewal), ADR-0029 (durable task claims), ADR-0026 (steering/staleness)

## Context

V5 (ADR-0038) split cognition into FAST/FULL to buy social latency, but FULL remains a
*social turn*: a bounded ReAct loop (`max_tool_calls=6`) with forced fail-closed
convergence, freshness validation against newer chatter (ADR-0026/0037), and a latency
budget tied to a live conversation. Three pressures exceed that shape:

1. **Depth ceiling.** Six tool calls is "answering with retrieval", not doing work.
   Verifying a claim across sources, digesting a feed, or exploring a topic takes
   dozens of steps and minutes — a result that the staleness gate would correctly
   reject if it ran inside a social episode.
2. **The agent direction.** Heartbeats, self-initiated ideas, exploration, and feed
   reading are the operator's stated goal. These are long-horizon executions, not
   replies; today the runtime has no execution body for them — only the trigger
   channel (`TaskProposal → RuntimeGate → TASK_DUE`) that `deferred_reflection_task`
   (ADR-0038 §9.4) already uses.
3. **Context pollution.** Tool churn inside the social context (search dumps, page
   bodies, error payloads) degrades the 200K social window (ADR-0037) and the group
   register. Real agents solve this with sub-agents whose isolated contexts absorb
   the churn and return a distilled result.

The core insight: **a social turn and a work turn are different lifecycles.** Work
must be lifted out of the social episode into a runtime-owned executor with its own
context, budgets, and lifecycle, whose results re-enter the scene as ordinary events
— after which normal cognition decides how (and whether) to speak them.

## Decision

### 1. AgentJob — a reserved task kind, proposed only through the gate

An AgentJob is a `TaskItem` with `payload.kind == "agent_job"`, carrying:

- `goal` — the work description (mirrors `TaskProposal.description`);
- `tool_hints` — optional list of tool names the proposer expects to matter;
- `context_refs` — origin episode/stimulus IDs for traceability (existing fields);
- `delay_seconds` — 0 for immediate work, or a future due time ("明早帮我查X").

Job creation reuses the existing proposal machinery end to end — no new authority
path. `TaskProposal` gains the payload kind; `RuntimeGate.evaluate_and_commit`
validates and commits it atomically as today. Legitimate proposers:

- a **FULL episode** whose judgement is "this burst asks for real work" — it proposes
  a job instead of struggling inside its social budget;
- **quiet-window reflection** (`deferred_reflection_task`, already shipped);
- the **operator** via the Control Plane.

FAST cannot propose jobs — its contract is physically unexpressive by design
(ADR-0038 §2). A future FAST `escalation_reason` enum (`needs_tool`, `hard_question`)
may sharpen the FULL trigger but never bypasses it.

### 2. AgentJobRunner — execution outside both the SceneActor and the social turn

A new runtime-owned component (`cognition/job_runner.py`). When the scheduler fires
`TASK_DUE` for a task whose payload kind is `agent_job`, the runtime dispatches to the
runner instead of a social cognition episode:

- It runs as an ordinary asyncio task owned by `AgentRuntime` — never inside the
  single-writer SceneActor (ADR-0004), never on the social reply path.
- It builds an **isolated context**: job brief (goal, origin, a compact scene
  snapshot), NOT the social session, NOT the raw 200K window. The job context is
  ephemeral and dies with the job; persistent truth stays in events/session/tasks
  (Invariant 3).
- It executes the same ReAct machinery as `SocialCognitionCore` (ADR-0035 semantics:
  malformed tool arguments and toolkit exceptions feed back as `role:"tool"` errors;
  forced final via `tool_choice="none"`) but with **work budgets**:
  `agent_job_max_tool_calls` (default ~24), `agent_job_wall_clock_seconds`
  (default ~300), and a token ceiling — all operator-configured deterministic
  ceilings (Invariant 10). Default routing tier: `deliberate`.
- Tool surface: the scope-guarded `RetrievalToolkit` plus registered plugin tools
  (web_search/read_page today; browser and code-exec in Phase 2), all under the
  existing PluginHost timeout/SSRF sandbox (ADR-0016/0030).

### 3. Observable lifecycle — events, not side channels

- **Start** is already observable: the committed `TASK_DUE` with payload
  `kind=agent_job`. The session reducer records it as a factual in-flight-job
  observation (deterministic, no social judgement) so subsequent FAST/FULL episodes
  in the scene *know the bot is working on X* — if someone asks "查到没", ordinary
  social cognition answers naturally. The job itself never sends anything.
- **Completion** emits a new `AGENT_JOB_COMPLETED` event, registered in
  `BurstAssembler._IMMEDIATE_EVENT_TYPES` alongside `TOOL_COMPLETED`. Mid-run
  `AGENT_JOB_PROGRESS` events are deferred to Phase 2 (throttled deterministically,
  never bursting on their own).

### 4. JobResult — pure data; the social layer speaks

The runner's final forced-final step distills the run into a compact `JobResult`:

```json
{
  "status": "completed | timeout | cancelled | interrupted",
  "summary": "distilled findings (size-capped, ~2-4K chars)",
  "follow_up_hint": "advisory only — what the job thinks should happen next"
}
```

`AGENT_JOB_COMPLETED` bursts route **directly to FULL** (consistent with
`TOOL_COMPLETED`): turning findings into speech and possibly proposing follow-ups
(chaining the next job, a condition-bound watch, memory candidates with the job
events as evidence) needs the expressive `SocialCognitionResult` contract; latency
is acceptable because the origin is asynchronous. The post-job episode — never the
job — owns every proposal. Jobs do not write memories, mutate session state, or send
messages (Invariants 1/2/6 untouched).

**Chaining is allowed but capped.** A job-completion episode may propose the next
job, but agent_jobs originating from an `AGENT_JOB_COMPLETED` episode are bounded by
a deterministic chain-depth cap and a per-scene cool-down window — an LLM cannot
build a self-perpetuating loop through the gate. Sanctioned recurrence is the
operator-granted heartbeat only (§6).

### 5. Cancellation and failure semantics

- **User steering:** the runner attaches an `EpisodeMailbox`-equivalent and checks
  cancellation at each step boundary (ADR-0002/0026 semantics); a human CANCEL
  steering event in the scene cancels the job (`status=cancelled`).
- **Wall-clock timeout:** the asyncio task is cancelled; `AGENT_JOB_COMPLETED`
  carries `status=timeout` with honest partial findings.
- **Runtime restart mid-job:** `status='running'` rows are recovered as
  `interrupted` at startup and a completion event records the interruption — no
  ghost re-execution; a re-run requires a fresh proposal through the gate.
- **Exactly-once:** task claiming keeps the ADR-0029 guarantee
  (`UPDATE ... WHERE id=? AND status='pending'`).

### 6. Heartbeats — the deliberate ADR-0034 exception

ADR-0034 forbids a due wake self-renewing without a newer human/plugin event.
Heartbeats are the one sanctioned recurrence, and only because they are
**operator-installed**: a `kind=heartbeat` recurring task is created exclusively via
the Control Plane, never by an LLM proposal, and re-arms itself on each fire by
construction. Whatever a beat's episode then proposes (including an agent_job)
passes the gate fresh like anything else — the anti-self-renewal invariant stays
intact for everything the model originates.

### 7. Deterministic ceilings and observability

- Per-scene concurrent jobs: 1 (serialized); global cap `agent_job_max_concurrent`
  (default 2). Queued jobs wait; they never spawn parallel workers per scene.
- Scope: a job inherits `allowed_scopes=[scene_id, "global-safe"]` — a job born in a
  group can never read private chats (Invariant 9, SQL-layer, unchanged).
- Shadow mode: jobs are internal work and run normally; only the post-job speech
  rides shadow suppression as usual (ADR-0023).
- Metrics: `agent_jobs_total / completed / cancelled / timed_out / interrupted`,
  `agent_job_tool_calls`, chain depth. Per-step traces land in `traces` with kind
  `agent_job` (tier/provider/latency/tokens/tool-call counts) — the Trace view
  answers "what did the bot actually do for those five minutes".

### 8. Phasing

1. **Phase 1 — core:** task payload kind, runner, `AGENT_JOB_COMPLETED` event,
   BurstAssembler registration, post-job FULL routing, steering cancellation,
   budgets, metrics/traces, deterministic tests (mock loop, no network).
2. **Phase 2 — hands:** browser plugin (playwright) as sensory + tool, code-exec
   sandbox tool, throttled progress events.
3. **Phase 3 — recurrence & control:** operator heartbeat tasks, Control Plane job
   view (in-flight/history/cancel).
4. **Phase 4 — skill distillation:** successful job trajectories (already fully
   recorded in `traces`) distilled into procedure memories / voice exemplars — the
   learning-loop counterpart, built on evidence the runtime already keeps.

## Consequences

- Execution depth stops being capped by social latency: minutes-long, dozens-step
  work becomes lawful without touching the staleness gate, the SceneActor, or the
  FAST reply budget. The agent direction (heartbeat/explore/feed) becomes instances
  of one loop: propose → gate → TASK_DUE → AgentJobRunner → result event → social
  cognition.
- The social context stays clean: tool churn lives and dies in the job's ephemeral
  context; the scene receives only factual lifecycle observations and the distilled
  result.
- Cost: two new event types, a reserved task kind, a new runner component, and
  restart-recovery semantics. Job spend is bounded only by operator ceilings — the
  defaults must be conservative until production data justifies raising them.
- Honest failure: timeout/cancel/interruption all surface as explicit statuses;
  partial findings are labelled partial, never silently completed.
- Open questions for review (blocking acceptance):
  1. Default budgets (tool calls 24 / wall clock 300s / chain depth 2?) and whether
     they live in `RuntimeConfig` or per-scene overrides.
  2. Should `AGENT_JOB_COMPLETED` route to FULL always, or FAST when `summary` is
     short and no follow-up is hinted?
  3. Browser engine choice for Phase 2 (playwright headless in-plugin vs. separate
     sandbox service).
  4. Whether Phase 4 distillation writes procedure memories under a new
     `MemoryKind` (requires enum extension) or reuses `habit`/`fact`.

## New domain terms (add to CONTEXT.md upon acceptance)

**AgentJob**, **AgentJobRunner**, **JobResult**, **Agent Job Chaining**,
**Heartbeat Task**
