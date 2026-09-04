# ADR-0034: Social Core Production Path and Durable Ambient Wake

- Status: Accepted
- Date: 2026-09-04
- Owners: LenBot maintainers

## Context

V4 removes the V3 Attention/Interest/ParticipationThread decision path and makes every valid social burst enter one Social Cognition Core. Ambient cognition also needs to survive restart without letting an LLM own a timer or creating a second, divergent copy of schedule state inside `GroupAgentSession`.

## Decision

`BurstAssembler → GroupAgentSession → SocialCognitionCore` is the sole production social path. The Social Core may return `SILENCE`, `SPEAK`, task proposals, retained attention, memory candidates, or one typed `future_attention` proposal. It never performs a side effect.

`future_attention` becomes real only when `RuntimeGate` converts it to a reserved `next_wake` `TaskProposal`, applies the deterministic minimum interval, and commits it through the existing atomic task transaction. The pending task row is the only durable authority for its due time and origin mode. Social Core receives a read-only `pending_next_wake` projection from that row; `GroupAgentSession` does not duplicate scheduling truth.

There may be at most one pending next-wake task per scene. A newer committed wake cancels the older row in the same transaction and replaces it in the scheduler heap. When claimed, the task emits the ordinary `TASK_DUE` event and re-enters Social Core. A due wake cannot propose another wake unless a new human or plugin social event was committed after the original observation cursor. Normal task proposals cannot use the reserved `next_wake` kind.

Retained attention remains session-owned soft working state. Expired items are pruned by the session reducer on the next event or cognition commit, before they can enter a new Social Core context; no separate cleanup service is introduced.

## Consequences

- Scheduler claim, restart recovery, Shadow origin, stale rejection, and action authority remain deterministic Runtime responsibilities.
- A model-requested wake time may differ from the committed due time because the Runtime minimum interval is authoritative.
- Idle expired retained items may remain serialized until the next lawful session transition, but they are never presented to a later cognition.
- ADR-0033's temporary Stage 2 Shadow/V3 dual-path behavior is superseded. ADR-0032's session-owned next-wake placeholder is replaced by the durable task projection described here.
