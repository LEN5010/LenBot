# ADR-0038: Fast/Full Social Cognition Split and the Voice Architecture

- Status: Accepted
- Date: 2026-09-05

## Context

Two production problems motivate V5:

1. **Assistant-flavoured replies.** The Social Cognition Core treats every burst as a
   `SocialCognitionResult` whose implicit shape is *perceive the whole scene → update the
   world model → decide → propose*. A group member reacting to "我刚把线上库删了" with
   "？" or "你最好是在开玩笑" does not need — and should not pay for — a full social
   world rebuild, person/relationship updates, and memory candidates. The heavyweight
   contract makes short social reactions the expensive, unusual case instead of the default.
2. **Latency on the critical path.** A casual reply costs: full context assembly
   (session snapshot + 200K-token raw window + full JSON schema) → one multi-hundred-token
   structured completion → gate. The common case should cost approximately one small-model
   completion with a tiny output.

Constraints carried forward: no rule-based attention engine, no keyword/relevance scoring
in place of LLM social judgement, no classifier/critic agents, one model call where one
suffices, Runtime keeps all side-effect authority, existing Event Store / SceneActor /
RuntimeGate / MemoryGate / Reflection foundations are reused.

## Decision

### 1. FAST / FULL routing — one entry path, two cognition depths

Every social burst still enters cognition through the existing
`BurstAssembler → AgentRuntime._on_burst → per-scene task loop` path. The runtime chooses
the depth **structurally**, never by social rules:

- `PROACTIVE_TASK` (TASK_DUE) and `TOOL_COMPLETED` bursts go **directly to FULL** —
  they are system-originated and require planning or tool synthesis.
- All human chat bursts go to **FAST** first.
- `PLUGIN_FACT` bursts (LIVE_STARTED/LIVE_ENDED) are social relevance judgements → FAST.

FAST is not a classifier. One small-model call understands the local social context and
either SILENCEs, SPEAKs with final messages, or escalates:

```json
{
  "decision": "silence | speak | full",
  "reason": "...",
  "messages": [{"content": "...", "reply_to": null, "expect_reply": false, "reply_target": null, "reply_intent": null}]
}
```

- `speak` requires ≥1 message; `silence` forbids messages; `full` carries no messages —
  the same speak/silence contract as the FULL result.
- On `full`, the FAST result is discarded (it was only a routing judgement) and the existing
  `SocialCognitionCore.execute` runs over the same burst. Escalation is one-way per burst;
  the FULL result is the only one that commits.
- Deliberate escalation inside FULL (tool complexity) is unchanged (ADR-0012 router half).

### 2. FAST context — minimal necessary input

`FastContextAssembler` builds, from the shared projection helpers:

```
【IDENTITY CORE】        static persona block (stable prefix → provider prompt caching)
【ADAPTIVE SELF STATE】  engagement / inclination / 你 N 秒前说过话 / 连续发言计数
【GROUP REGISTER】       this scene's factual style statistics (see §4)
【RECENT RAW CHAT】      newest-first projected raw events under a small token budget
【OPEN LOOPS】           id / target / intent of active loops (FAST may resolve none — it only reads)
【VOICE EXAMPLES】       2–4 rotated curated exemplars (see §5)
【CURRENT BURST】        projected burst + mention/reply flags
```

FAST never loads the full `SocialWorldState`, relationship models, memory toolkit, ReAct
loop, or task planning. Its output carries **no** world/person/memory/task proposals — the
schema physically cannot express them.

### 3. Immediate vs deferred state

**Immediate state (strict, FAST path):** cursor advance, bot's own message facts, open
loops (two-phase commit unchanged), consecutive-bot counters, staleness, gate authority.
FAST results commit through a new `submit_fast_cognition` SceneActor command that:

- validates the same observation-cursor staleness contract as FULL;
- emits `SOCIAL_COGNITION_RECORDED` with `payload.kind == "fast"` (state changes still
  originate from an event — Invariant 1);
- applies `GroupAgentSessionReducer.apply_fast_cognition`: version++, `last_cognized_event_rowid`,
  retained-attention pruning, and nothing else — subjective social world is untouched.

**Deferred cognition (off the reply critical path):** group mood, topic evolution, person
and relationship patterns, semantic memory, group identity. Two existing/extended channels:

- The quiet-window reflector (ADR-0019/0028) already produces L1 episodes + L2 memories.
- It now also proposes a merge-only `SocialWorldPatch` (mood, topics open/close,
  dynamics notes, group-identity evolution). The runtime submits it to the SceneActor as a
  `DeferredSessionPatchCommand`; the actor merges it into `GroupAgentSession` in the lawful
  commit path. Merge-only (add/close-by-id, never wholesale replace) keeps it causally safe
  against a fresher FULL snapshot; FULL results continue to replace the world state
  wholesale as before.

### 4. Group Register (per-scene style statistics)

`GroupAgentSession.group_register` is maintained by the deterministic reducer from human
messages only (factual counting, not social judgement): rolling window of message lengths
(cap 100), fragment/punctuation/emoji/question counts, and common short reactions (≤4 chars,
top-K). Ratios and the median are derived at prompt time. The register is **style context
for the model**, never a decision input — no threshold anywhere reads it.

### 5. Dynamic Voice Exemplars

New `voice_exemplars` table (id, scene_id — empty means global —, context, content, tag,
enabled, use_count, last_used_at). Selection at prompt time picks the 2–4 least-recently-used
enabled exemplars matching the scene (global ones fill the remainder) and bumps their
use counters asynchronously. Rotation prevents fixed few-shot overfitting; the prompt
instructs density/tone imitation, never verbatim reuse. CRUD is exposed via the Control
Plane so approved real replies can be curated over time (a future preference dataset lands
in the same table).

### 6. Identity Core

The persona prompt becomes layered: a structured **Identity Core** (long-term behavioural
tendencies — how it treats friends vs strangers, when it is serious vs dismissive, humour
style, conflict style) composed with the existing short style line, the adaptive self
state, the group register, and exemplars. Low-information adjectives ("自然/幽默") are
replaced by observable behavioural tendencies in the default. `identity_core` is operator
editable via the Control Plane and persisted in `persona_config`.

### 7. Anti-slop guard

`runtime/style_guard.py` is a pure local detector over the bot's recent messages per scene:
exact-duplicate detection, repeated openers/closers, repeated n-grams, length convergence.
It records metrics always and triggers **at most one** corrective FAST retry on a strong
anomaly (duplicate or 3-of-last-5 same opener). No per-reply LLM critic; sampler settings
stay a per-provider operations concern.

### 8. Latency & routing metrics

`RuntimeMetrics` gains: `bursts_total`, `cognition_fast_calls`, `cognition_fast_speak`,
`cognition_fast_silence`, `cognition_fast_to_full`, `cognition_full_calls`,
`cognition_deliberate_calls`, `style_slop_flags`, `style_retries`, and phase latency
percentiles — `event→burst`, `burst→model_request`, `model_total`, `parse`, `gate`,
`queue→send`, `end_to_end` — recorded into the per-episode trace payload. FAST runs on the
`fast` routing tier (`RoutingConfig.fast`, falling back to `normal` when unset) so operators
can point it at a small model; the FULL path keeps normal/deliberate.

## Consequences

- A casual burst costs one small-model completion with a tiny JSON output; complex bursts
  keep the full ReAct/retrieval/deliberate machinery. Model calls per burst become
  measurable and tunable.
- Social world state on FAST-only scenes is updated by reflection (deferred) instead of
  per-reply; FULL always sees the raw token-budgeted window, so staleness of subjective
  state does not corrupt factual grounding.
- The speak/silence contract, RuntimeGate ceilings, two-phase open-loop commit, shadow
  mode, and preemption semantics are unchanged — FAST results ride the exact same
  authority paths.
- `SocialCognitionResult` remains the FULL contract; the FAST contract is intentionally
  unexpressive (no evidence IDs → no evidence validation needed beyond burst scope).
- Group register and exemplar injection add small deterministic cost to context assembly
  but no model calls.
