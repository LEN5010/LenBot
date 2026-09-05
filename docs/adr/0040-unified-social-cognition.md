# ADR-0040: Unified Social Cognition and Continuous Fulfilment

- Status: Accepted for implementation (operator-approved plan)
- Date: 2026-09-05
- Audience: Runtime maintainers

## Context

FAST could promise an action without expressing a task and omitted social working state.
The first production run also rejected 80 of 189 cognitive results after new events.
Restarting the whole turn discarded useful retrieval work. Reflection's task write
occurred after its cursor commit, leaving a lost-work window.

## Decision

Use one Social Core with sparse, evidence-checked social patches and on-demand tools.
Normal/deliberate are capability tiers of that same core; escalation is an explicit
model request. FAST contracts and production routing are removed, not feature-flagged.
The ephemeral turn retains tool messages while incorporating committed new events.
SceneActor still checks the observed cursor at commit; unread events never authorize
an old answer. Natural-language task cancellation is model judgement, not substring matching.

Tasks retain requester, target, source evidence and absolute due time. Gate owns create,
update, cancel and result proposals. Acknowledgements reference committed proposals;
fulfilment messages reference existing tasks. Delivery confirmation, not TASK_DUE, closes
a notification. Ambiguous delivery is recorded and never automatically retried.
Task claims persist a pending TASK_DUE envelope atomically. Restart reuses its event ID.

Reflection produces memories, a versioned social patch and evidence-backed review items,
never executable tasks. Its cursor and durable review envelope commit together. Social
Core decides what to do with a review against current tasks and current time. Old expired
promises are not converted into delayed reminders automatically.

Session state remains owned by SceneActor, scheduling rows by Gate/EventStore and
claiming by Scheduler. SQL scene scope, evidence validation, shadow origin and action
serialization remain authoritative. Replay uses an isolated runtime with shadow delivery.

## Alternatives and consequences

Sharing more context with FAST retains two contracts and an unnecessary escalation trip;
we choose one core, accepting potentially higher single-call latency. No vector retrieval,
workflow framework, autonomous exploration or AgentJob implementation is introduced.
The existing provider routes migrate once by removing fast. Historical traces are retained.
Old triggered tasks are review-required, never presumed completed.

## Validation and rollout

Use deterministic in-flight-event, task recovery, scope, reflection and delivery tests;
then run real-provider transcript evaluation in an isolated shadow database. Existing
production data must be backed up before migration. Do not restore live delivery until
the operator has reviewed shadow results. Rollback uses the pre-migration database backup
with the old revision; do not run an old binary against the new task states.

## Implementation checkpoint — 2026-09-06

Unified cognition, atomic session/proposal commit, durable claim envelopes, task delivery
states and recovery, reflection review envelopes, production-runtime replay, persona
and voice editing, and task editing are implemented. The frontend build passes.
The attempted real-provider replay failed on connectivity; it is not a behavioural pass.
The operator requested a commit and clean-data machine trial before further work.
Production conversation/session/memory/task/trace data and the old run log were cleared
without a backup, as requested; provider/persona/OneBot/login configuration was retained.
