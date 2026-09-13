import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional, Any, Callable, Literal
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.scenes.models import SceneSession
from len_bot.actions.models import ActionItem, ActionType, AllMentionSegment
from len_bot.actions.queue import ActionQueue
from len_bot.events.store import EventStore
from len_bot.scheduler.models import TaskItem
from len_bot.memory.models import MemoryItem

logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_OUTCOME = 3
MAX_CONSECUTIVE_BOT_MESSAGES = 4

@dataclass
class ProposalCommit:
    episode_id: str
    scene_id: str
    base_scene_version: int
    outcome: EpisodeOutcome
    mailbox: EpisodeMailbox

@dataclass
class CommittedProposal:
    episode_id: str
    scene_id: str
    committed_tasks: list[TaskItem] = field(default_factory=list)
    resolved_loop_ids: list[str] = field(default_factory=list)
    committed_memories: list[MemoryItem] = field(default_factory=list)
    outcome: Optional[EpisodeOutcome] = None
    origin_mode: str = "live"
    response_actor_ids: list[list[str]] = field(default_factory=list)
    source_started_at: list[float | None] = field(default_factory=list)


@dataclass
class ActionPublication:
    action_id: str
    batch_index: int
    origin_event_id: str | None
    requester_qq_uid: str | None
    job_id: str | None
    job_revision: int | None
    operation_ref: str | None
    fulfils_task_id: str | None
    acknowledges_task_id: str | None = None
    status: Literal["not_enqueued", "enqueued", "enqueue_unknown"] = "not_enqueued"


@dataclass
class PublicationRecord:
    status: Literal["pending", "completed", "failed", "interrupted", "not_repeated"] = "pending"
    phase: Literal["not_started", "awaiting_publication", "action_preparation", "scheduler_schedule",
                   "scheduler_sync", "action_enqueue", "completed", "previous_commit",
                   "commit_acknowledgement"] = "not_started"
    scheduler_status: Literal["not_required", "not_started", "synchronized", "failed", "interrupted"] = "not_required"
    scheduled_task_ids: list[str] = field(default_factory=list)
    actions: list[ActionPublication] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None

class GateDecision:
    def __init__(
        self,
        disposition: FinalDisposition,
        reason: str,
        actions_enqueued: int = 0,
        committed_proposal: Optional[CommittedProposal] = None,
        accepted: bool = True
    ):
        self.disposition = disposition
        self.reason = reason
        self.actions_enqueued = actions_enqueued
        self.committed_proposal = committed_proposal
        self.accepted = accepted
        self.action_ids: list[str] = []
        self.commit_event_id: str | None = None
        self.publication: PublicationRecord | None = None
        self.scene_session: SceneSession | None = None

    def record(self) -> dict:
        return {"accepted": self.accepted, "committed": self.committed_proposal is not None,
                "commit_event_id": self.commit_event_id, "disposition": self.disposition.value,
                "reason": self.reason, "action_ids": self.action_ids,
                "actions_enqueued": self.actions_enqueued,
                "publication": asdict(self.publication) if self.publication else None}

class RuntimeGate:
    def __init__(
        self,
        event_store: EventStore,
        action_queue: ActionQueue,
        scheduler: Optional[Any] = None,
        memory_gate: Optional[Any] = None,
        metrics: Optional[Any] = None,
        origin_mode_provider: Optional[Callable[[], str]] = None,
        bot_actor_id: str = "",
        *,
        open_loop_ttl_seconds: float,
    ):
        self.event_store = event_store
        self.action_queue = action_queue
        self.scheduler = scheduler
        self.memory_gate = memory_gate
        self.metrics = metrics
        self.origin_mode_provider = origin_mode_provider
        self.bot_actor_id = bot_actor_id
        self.open_loop_ttl_seconds = open_loop_ttl_seconds
        self.jobs_enabled_probe = lambda: True
        self.validate_job_resume = None
        self.validate_plugin_origin = None
        self.scene_policy = None
        self.capability_authority = None
        self._publication_locks: dict[str, asyncio.Lock] = {}

    def _capability_refusal(self, proposal, scene_id: str, *, on_behalf_of=None) -> str | None:
        """The ordered capability check for one non-human work proposal.

        Check order stays in one place: scene, then the real source and
        initiator, then the current grant.  Later steps (original revision,
        the operation's own requirement, data scope, budget) are enforced
        where they already live and are not duplicated here.

        A control on an existing work passes `on_behalf_of`, the identity that
        work was created under: a resume starts new provider calls, so it is
        checked against the grants that exist now, exactly like the creation
        it continues.  Revoking a grant therefore blocks the next execution of
        a work it once authorized, without rewriting anything the work has
        already done.  The identity comes from the work itself, never from the
        control's own proposal, so a control cannot lend its permissions to a
        work someone else's origin created.
        """
        from len_bot.runtime.capabilities import Capability, subject_for
        try:
            subject = subject_for(on_behalf_of if on_behalf_of is not None else proposal.initiator, scene_id)
        except ValueError as error:
            return str(error)
        authority = self.capability_authority
        operation = proposal.work_operation
        required = authority.required_for_work(operation) or (Capability.LONG_WORK,)
        for capability in required:
            decision = authority.check(capability, subject, now=self.event_store.clock())
            if not decision.allowed:
                return f"Capability check refused at step {decision.step}: {decision.reason}"
        return None

    async def evaluate_and_commit(
        self,
        outcome: EpisodeOutcome,
        mailbox: EpisodeMailbox,
        current_scene_state: SceneSession,
        proposal_commit: Optional[ProposalCommit] = None,
        scene_commit: dict | None = None,
        operator_control: bool = False,
    ) -> GateDecision:
        if proposal_commit is None:
            proposal_commit = ProposalCommit(
                episode_id=mailbox.episode_id,
                scene_id=current_scene_state.scene_id,
                base_scene_version=mailbox.base_scene_version,
                outcome=outcome,
                mailbox=mailbox
            )

        # The Actor supplies operator_control for authenticated management.
        # It permits state changes without creating a QQ conversation identity.
        if operator_control and (outcome.disposition != FinalDisposition.SILENCE or outcome.message_proposals):
            return GateDecision(FinalDisposition.SILENCE, 'Operator controls cannot submit messages', accepted=False)
        read_ids = scene_commit['event'].payload['source_event_ids'] if scene_commit else []
        source_events = {event.id:event for event in await self.event_store.events_by_ids(
            current_scene_state.scene_id,[message.source_event_id for message in outcome.message_proposals
                if message.source_event_id],current_scene_state.last_observed_event_rowid)}
        if mailbox.output_kind == 'chat' and not operator_control:
            ids = {message.source_event_id for message in outcome.message_proposals if message.source_event_id}
            ids.update(outcome.handled_source_event_ids)
            if not ids.issubset(read_ids):
                return GateDecision(FinalDisposition.SILENCE, 'Message request source was not read in this turn', accepted=False)
            source_events = {event.id:event for event in await self.event_store.events_by_ids(
                current_scene_state.scene_id, list(ids), current_scene_state.last_observed_event_rowid)}
            requesters = set()
            requesters.update(event.actor_id.removeprefix('user:') for event in source_events.values()
                if event.id in outcome.handled_source_event_ids and event.actor_id.startswith('user:') and event.actor_id != self.bot_actor_id)
            for message in outcome.message_proposals:
                source = source_events.get(message.source_event_id)
                if (source is None or source.event_type.value not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                        or source.actor_id == self.bot_actor_id or not message.requester_qq_uid
                        or source.actor_id != 'user:' + message.requester_qq_uid):
                    return GateDecision(FinalDisposition.SILENCE, 'Each chat message needs its own human request source', accepted=False)
                requesters.add(message.requester_qq_uid)
                if message.job_id and message.fulfils_task_id:
                    job=await self.event_store.get_job(message.job_id,current_scene_state.scene_id)
                    if job:requesters.add(job['requester_qq_uid'])
            requesters.update(proposal.requester_qq_uid for proposal in outcome.job_proposals
                              if proposal.requester_qq_uid is not None)
            task_rows = {task['id']:task for task in await self.event_store.scene_tasks(current_scene_state.scene_id)}
            for proposal in outcome.task_proposals:
                if proposal.operation == 'create':
                    requesters.add(proposal.payload.get('requester_qq_uid'))
                elif proposal.task_id in task_rows:
                    requesters.add(task_rows[proposal.task_id]['payload'].get('requester_qq_uid'))
            if self.scene_policy and (not self.scene_policy.enabled(current_scene_state.scene_id)
                    or any(not self.scene_policy.chat_allowed(current_scene_state.scene_id, requester)
                           for requester in requesters)):
                return GateDecision(FinalDisposition.SILENCE, 'A request source no longer has chat eligibility in this group', accepted=False)

        # SceneActor owns input freshness; Gate still honors explicit cancellation.
        if mailbox.is_cancelled():
            reason = mailbox.cancellation_reason() or "Episode cancelled by steering"
            logger.info("Gate rejected response due to cancellation: %s", reason)
            if self.metrics:
                self.metrics.inc_social("cancellations_honored")
            return GateDecision(FinalDisposition.SILENCE, f"Gate rejected stale response: {reason}", accepted=False)

        if outcome.disposition == FinalDisposition.ACTION:
            if len(outcome.message_proposals)+mailbox.messages_committed > MAX_MESSAGES_PER_OUTCOME:
                return GateDecision(
                    FinalDisposition.SILENCE,
                    "All checkpoints share the episode message limit",
                    accepted=False,
                )
            if (mailbox.output_kind == 'chat' and current_scene_state.consecutive_bot_messages >= MAX_CONSECUTIVE_BOT_MESSAGES
                    and not all(message.fulfils_task_id for message in outcome.message_proposals)):
                return GateDecision(
                    FinalDisposition.SILENCE,
                    "Gate rejected hard anti-loop ceiling: consecutive bot messages",
                    accepted=False,
                )

        # Carry the actual live/shadow origin into the transaction.
        curr_origin = self.origin_mode_provider() if self.origin_mode_provider else "live"
        if mailbox.origin_mode == "shadow":
            curr_origin = "shadow"
        # Resolve references before any transaction or visible acknowledgement.
        if not self.jobs_enabled_probe() and any(p.operation == 'create' for p in outcome.job_proposals):
            return GateDecision(FinalDisposition.SILENCE, "Information work is disabled", accepted=False)
        if self.capability_authority:
            for proposal in outcome.job_proposals:
                if proposal.operation == 'create':
                    if proposal.human_initiator is not None:
                        continue
                    # A non-human source never reuses the human request path.
                    # It needs its own configured grant; an absent, disabled or
                    # expired grant denies and the work is not created.
                    refusal = self._capability_refusal(proposal, current_scene_state.scene_id)
                elif proposal.operation == 'resume':
                    # A resume starts new provider calls under the identity the
                    # work was created with, so the grant that identity has now
                    # is what decides it too.  Revise and cancel are not
                    # starting execution and are left exactly as they were, and
                    # the store's own re-validation keeps the revision and the
                    # spend that must not restart.
                    current = await self.event_store.get_job(proposal.job_id, current_scene_state.scene_id)
                    on_behalf_of = self.event_store.initiator_of(current) if current else None
                    if on_behalf_of is None or on_behalf_of.principal_type == 'human':
                        continue
                    refusal = self._capability_refusal(proposal, current_scene_state.scene_id,
                                                       on_behalf_of=on_behalf_of)
                else:
                    continue
                if refusal:
                    return GateDecision(FinalDisposition.SILENCE, refusal, accepted=False)
        if self.validate_job_resume:
            for proposal in outcome.job_proposals:
                if proposal.operation == 'resume':
                    try:
                        await self.validate_job_resume(proposal.job_id, current_scene_state.scene_id)
                    except (ValueError, LookupError) as error:
                        return GateDecision(FinalDisposition.SILENCE, str(error), accepted=False)
        proposals=[*outcome.task_proposals,*outcome.job_proposals,*outcome.memory_proposals]
        proposal_ids = [proposal.proposal_id for proposal in proposals if proposal.proposal_id]
        if len(proposal_ids) != len(set(proposal_ids)):
            return GateDecision(FinalDisposition.SILENCE, "Duplicate transaction proposal references", accepted=False)
        creation_refs={proposal.proposal_id for proposal in [*outcome.task_proposals,*outcome.job_proposals]
                       if proposal.operation=='create' and proposal.proposal_id}
        operation_refs=({proposal.proposal_id for proposal in [*outcome.task_proposals,*outcome.job_proposals]
                         if proposal.operation!='create' and proposal.proposal_id}
                        | {proposal.proposal_id for proposal in outcome.memory_proposals if proposal.proposal_id})
        action_ids = [str(uuid.uuid4()) for _ in outcome.message_proposals]
        response_actors = []
        for message in outcome.message_proposals:
            targets = set()
            targets.update(message.addressed_to)
            response_actors.append(sorted(targets))
        if scene_commit:
            scene_commit['event'].payload['response_actor_ids'] = response_actors
            scene_commit['event'].payload['action_ids'] = action_ids
        for source in outcome.source_outcomes:
            if any(index<0 or index>=len(action_ids) for index in source.message_indices):
                return GateDecision(FinalDisposition.SILENCE,'Source outcome references a missing message',accepted=False)
            source.action_ids=[action_ids[index] for index in source.message_indices]
        deliveries = {}
        acknowledgements = {}
        operation_confirmations = {}
        for index, message in enumerate(outcome.message_proposals):
            if message.task_ref and message.task_ref not in creation_refs:
                return GateDecision(FinalDisposition.SILENCE, "Acknowledgement has no task proposal", accepted=False)
            if message.task_ref:
                if message.task_ref in acknowledgements:
                    return GateDecision(FinalDisposition.SILENCE, "One acknowledgement per task proposal", accepted=False)
                acknowledgements[message.task_ref] = action_ids[index]
            if message.operation_ref:
                if message.operation_ref not in operation_refs:
                    return GateDecision(FinalDisposition.SILENCE, "Operation confirmation has no staged control or memory operation", accepted=False)
                if message.operation_ref in operation_confirmations:
                    return GateDecision(FinalDisposition.SILENCE, "One confirmation per operation", accepted=False)
                operation_confirmations[message.operation_ref]=action_ids[index]
            if message.fulfils_task_id:
                if message.job_id and message.job_id != message.fulfils_task_id:
                    return GateDecision(FinalDisposition.SILENCE, "Job reply cannot fulfil another task", accepted=False)
                if message.fulfils_task_id in deliveries:
                    return GateDecision(FinalDisposition.SILENCE, "One fulfilment message per task", accepted=False)
                deliveries[message.fulfils_task_id] = action_ids[index]

        # 2. Authoritative Database Commit (Tasks, Open Loops, Memories) - All-or-Nothing Atomic Transaction
        try:
            committed_tasks, resolved_loops, committed_mems, resolved_outcome = await self.event_store.commit_proposal_transaction(
                episode_id=proposal_commit.episode_id,
                scene_id=proposal_commit.scene_id,
                outcome=proposal_commit.outcome,
                deliveries=deliveries,
                acknowledgements=acknowledgements,
                operation_confirmations=operation_confirmations,
                scene_commit=scene_commit,
                origin_mode=curr_origin,
                bot_actor_id=self.bot_actor_id,
                through_rowid=(scene_commit["event"].metadata["through_event_rowid"]
                               if scene_commit else current_scene_state.last_observed_event_rowid),
            )
        except Exception as e:
            logger.warning(
                "Atomic proposal commit transaction failed and rolled back on Scene %s: %s",
                proposal_commit.scene_id, e
            )
            return GateDecision(
                FinalDisposition.SILENCE,
                f"Gate rejected due to transaction rollback: {e}",
                accepted=False
            )

        # A returned decision describes the durable transaction only. The Actor
        # adopts its scene candidate before the caller starts publication.
        proposal_commit.outcome = resolved_outcome
        committed_proposal = CommittedProposal(
            episode_id=proposal_commit.episode_id, scene_id=proposal_commit.scene_id,
            committed_tasks=committed_tasks, resolved_loop_ids=resolved_loops,
            committed_memories=committed_mems, outcome=resolved_outcome,
            origin_mode=curr_origin, response_actor_ids=response_actors,
            source_started_at=[source_events[message.source_event_id].timestamp
                if message.source_event_id in source_events else mailbox.source_started_at
                for message in resolved_outcome.message_proposals],
        )
        decision = GateDecision(resolved_outcome.disposition,
            ("Operator controls committed: " if operator_control else "Conversation committed: ")
            + resolved_outcome.decision_reason, committed_proposal=committed_proposal)
        decision.action_ids = action_ids
        decision.commit_event_id = scene_commit['event'].id if scene_commit else None
        decision.publication = PublicationRecord(
            scheduler_status="not_started" if self.scheduler and (
                resolved_outcome.task_proposals or resolved_outcome.job_proposals) else "not_required",
            actions=[ActionPublication(action_id=action_ids[index], batch_index=index,
                origin_event_id=message.source_event_id or mailbox.origin_stimulus_id,
                requester_qq_uid=message.requester_qq_uid if message.source_event_id else mailbox.requester_qq_uid,
                job_id=message.job_id, job_revision=message.job_revision,
                operation_ref=message.operation_ref, fulfils_task_id=message.fulfils_task_id)
                for index, message in enumerate(resolved_outcome.message_proposals)],
        )
        return decision

    async def publish_committed(self, decision: GateDecision, mailbox: EpisodeMailbox) -> None:
        """Publish this accepted turn once, preserving every durable action ID."""
        committed = decision.committed_proposal
        publication = decision.publication
        if not decision.accepted or committed is None or publication is None:
            raise ValueError("Publication requires an accepted, committed proposal")
        if publication.status == "interrupted" and publication.phase == "commit_acknowledgement":
            raise asyncio.CancelledError(publication.error)
        if publication.status == "not_repeated":
            return
        if publication.status != "pending" or publication.phase != "not_started":
            raise ValueError("This committed turn's publication has already been attempted")
        if committed.episode_id != mailbox.episode_id or committed.scene_id != mailbox.scene_id:
            raise ValueError("Publication mailbox does not belong to the committed turn")
        publication.phase = "awaiting_publication"
        try:
            # Actor commits are ordered; keep their publication in the same
            # per-scene order when preparation or Scheduler sync must await.
            lock = self._publication_locks.setdefault(committed.scene_id, asyncio.Lock())
            async with lock:
                publication.phase = "action_preparation"
                # Complete every action before any is enqueued. No partial batch is
                # published merely because a later message cannot be assembled.
                actions = await self._prepare_actions(decision, mailbox)
                for action, record in zip(actions, publication.actions):
                    record.job_id, record.job_revision = action.job_id, action.job_revision
                    record.acknowledges_task_id = action.acknowledges_task_id
                if self.scheduler and publication.scheduler_status == "not_started":
                    publication.phase = "scheduler_schedule"
                    for task in committed.committed_tasks:
                        self.scheduler.schedule_task(task)
                        publication.scheduled_task_ids.append(task.id)
                    publication.phase = "scheduler_sync"
                    await self.scheduler._sync_from_db()
                    self.scheduler._wake_event.set()
                    publication.scheduler_status = "synchronized"
                publication.phase = "action_enqueue"
                for action, record in zip(actions, publication.actions):
                    # If enqueue raises after accepting, no success receipt was
                    # returned. Keep that action distinct from untouched actions.
                    record.status = "enqueue_unknown"
                    self.action_queue.enqueue(action)
                    record.status = "enqueued"
                    decision.actions_enqueued += 1
                publication.phase = "completed"
                publication.status = "completed"
        except (Exception, asyncio.CancelledError) as error:
            interrupted = isinstance(error, asyncio.CancelledError)
            publication.status = "interrupted" if interrupted else "failed"
            publication.error = str(error) or type(error).__name__
            publication.error_type = type(error).__name__
            if publication.phase in {"scheduler_schedule", "scheduler_sync"}:
                publication.scheduler_status = "interrupted" if interrupted else "failed"
            logger.error("Committed turn %s publication %s at %s: %s; actions=%s",
                committed.episode_id, publication.status, publication.phase,
                publication.error, [(item.action_id, item.status) for item in publication.actions])
            if interrupted:
                raise

    async def _prepare_actions(self, decision: GateDecision, mailbox: EpisodeMailbox) -> list[ActionItem]:
        committed = decision.committed_proposal
        outcome = committed.outcome
        scene_id = committed.scene_id
        action_ids = decision.action_ids
        actions = []
        now = self.event_store.clock()
        task_rows = {row['id']: row for row in await self.event_store.scene_tasks(scene_id)} if any(
            message.fulfils_task_id or message.task_ref for message in outcome.message_proposals) else {}
        for index, msg in enumerate(outcome.message_proposals):
            associated_loop = None
            if msg.expect_reply and msg.reply_target:
                associated_loop = {
                    "id": f"loop_{uuid.uuid4().hex[:10]}",
                    "scene_id": scene_id,
                    "target_actor_id": msg.reply_target,
                    "intent": msg.reply_intent or "general_response",
                    "source_event_id": "", # Will be filled by SceneActor on MESSAGE_SENT
                    "source_stimulus_id": msg.source_event_id,
                    "status": "active",
                    "created_at": now,
                    "expires_at": now + self.open_loop_ttl_seconds,
                    "resume_state": outcome.resume_state.model_dump(mode='json') if outcome.next_action=='wait' and outcome.resume_state else None,
                }

            action_type = (
                ActionType.SEND_PRIVATE_MESSAGE
                if scene_id.startswith("private:")
                else ActionType.SEND_GROUP_MESSAGE
            )
            action_origin = "shadow" if (committed.origin_mode == "shadow" or getattr(mailbox, "origin_mode", "live") == "shadow") else "live"
            if msg.fulfils_task_id and task_rows[msg.fulfils_task_id]["origin_mode"] == "shadow":
                action_origin = "shadow"
            job_id, job_revision = msg.job_id, msg.job_revision
            acknowledged_task_id = None
            if msg.task_ref:
                # Proposal refs repeat across turns; the transaction binds this unique action ID.
                task = next((row for row in task_rows.values() if row["payload"].get("ack_action_id") == action_ids[index]), None)
                if task is None:
                    raise ValueError('Committed acknowledgement task is missing')
                acknowledged_task_id = task['id']
            segments = list(msg.segments)
            if mailbox.plugin_origin and mailbox.mention_all:
                segments.insert(0, AllMentionSegment())
            action = ActionItem(
                source_started_at=committed.source_started_at[index],
                id=action_ids[index],
                fulfils_task_id=msg.fulfils_task_id,
                acknowledges_task_id=acknowledged_task_id,
                operation_ref=msg.operation_ref,
                action_type=action_type,
                scene_id=scene_id,
                segments=segments,
                output_kind='plugin' if msg.plugin_origin or mailbox.plugin_origin else mailbox.output_kind,
                plugin_origin=msg.plugin_origin or mailbox.plugin_origin,
                requester_qq_uid=msg.requester_qq_uid if msg.source_event_id else mailbox.requester_qq_uid,
                origin_event_id=msg.source_event_id or mailbox.origin_stimulus_id,
                command_id=mailbox.command_id,
                announcement_member=mailbox.announcement_member,
                episode_id=committed.episode_id,checkpoint_index=outcome.checkpoint_index,
                batch_id=decision.commit_event_id.removeprefix('turn:'),
                batch_index=index, batch_size=len(outcome.message_proposals),
                reply_to=msg.reply_to,
                response_actor_ids=committed.response_actor_ids[index],
                release_focus_actor_ids=outcome.release_focus_actor_ids,
                associated_open_loop=associated_loop,
                origin_mode=action_origin,
                job_id=job_id, job_revision=job_revision,
            )
            actions.append(action)

        return actions
