import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional, Any, Callable
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
        self.validate_native_origin = None
        self.scene_policy = None

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
        if (self.scene_policy and mailbox.output_kind == 'chat' and not operator_control
                and not self.scene_policy.chat_allowed(current_scene_state.scene_id, mailbox.requester_qq_uid)):
            return GateDecision(FinalDisposition.SILENCE, 'This requester no longer has chat eligibility in this group', accepted=False)

        # SceneActor owns input freshness; Gate still honors explicit cancellation.
        if mailbox.is_cancelled():
            reason = mailbox.cancellation_reason() or "Episode cancelled by steering"
            logger.info("Gate rejected response due to cancellation: %s", reason)
            if self.metrics:
                self.metrics.inc_social("cancellations_honored")
            return GateDecision(FinalDisposition.SILENCE, f"Gate rejected stale response: {reason}", accepted=False)

        if outcome.disposition == FinalDisposition.ACTION:
            if len(outcome.message_proposals) > MAX_MESSAGES_PER_OUTCOME:
                return GateDecision(
                    FinalDisposition.SILENCE,
                    "Gate rejected hard anti-spam ceiling: too many messages in one outcome",
                    accepted=False,
                )
            if mailbox.output_kind == 'chat' and current_scene_state.consecutive_bot_messages >= MAX_CONSECUTIVE_BOT_MESSAGES:
                return GateDecision(
                    FinalDisposition.SILENCE,
                    "Gate rejected hard anti-loop ceiling: consecutive bot messages",
                    accepted=False,
                )

        # Carry the actual live/shadow origin into the transaction.
        curr_origin = self.origin_mode_provider() if self.origin_mode_provider else "live"
        if mailbox.origin_mode == "shadow":
            curr_origin = "shadow"
        if curr_origin == "shadow":
            for tp in proposal_commit.outcome.task_proposals:
                tp.origin_mode = "shadow"

        # Resolve references before any transaction or visible acknowledgement.
        if not self.jobs_enabled_probe() and any(p.operation in {"create", "resume"} for p in outcome.job_proposals):
            return GateDecision(FinalDisposition.SILENCE, "Information work is disabled", accepted=False)
        if self.validate_job_resume:
            for proposal in outcome.job_proposals:
                if proposal.operation == 'resume':
                    try:
                        await self.validate_job_resume(proposal.job_id, current_scene_state.scene_id)
                    except (ValueError, LookupError) as error:
                        return GateDecision(FinalDisposition.SILENCE, str(error), accepted=False)
        proposal_ids = [tp.proposal_id for tp in [*outcome.task_proposals, *outcome.job_proposals] if tp.proposal_id]
        if len(proposal_ids) != len(set(proposal_ids)):
            return GateDecision(FinalDisposition.SILENCE, "Duplicate task proposal references", accepted=False)
        action_ids = [str(uuid.uuid4()) for _ in outcome.message_proposals]
        read_ids = scene_commit['event'].payload['source_event_ids'] if scene_commit else []
        response_actors = []
        for message in outcome.message_proposals:
            targets = set()
            if mailbox.output_kind != 'chat':
                response_actors.append([])
                continue
            if message.reply_to:
                target = await self.event_store.read_reply_actor(current_scene_state.scene_id, message.reply_to, read_ids)
                if target and target != self.bot_actor_id:
                    targets.add(target)
            if message.reply_target:
                targets.add(message.reply_target)
            response_actors.append(sorted(targets or mailbox.interaction_actors))
        if scene_commit:
            scene_commit['event'].payload['response_actor_ids'] = response_actors
        deliveries = {}
        acknowledgements = {}
        for index, message in enumerate(outcome.message_proposals):
            if message.task_ref and message.task_ref not in proposal_ids:
                return GateDecision(FinalDisposition.SILENCE, "Acknowledgement has no task proposal", accepted=False)
            if message.task_ref:
                if message.task_ref in acknowledgements:
                    return GateDecision(FinalDisposition.SILENCE, "One acknowledgement per task proposal", accepted=False)
                acknowledgements[message.task_ref] = action_ids[index]
            if message.fulfils_task_id:
                if message.job_id and message.job_id != message.fulfils_task_id:
                    return GateDecision(FinalDisposition.SILENCE, "Job reply cannot fulfil another task", accepted=False)
                if message.fulfils_task_id in deliveries:
                    return GateDecision(FinalDisposition.SILENCE, "One fulfilment message per task", accepted=False)
                deliveries[message.fulfils_task_id] = action_ids[index]

        # 2. Authoritative Database Commit (Tasks, Open Loops, Memories) - All-or-Nothing Atomic Transaction
        try:
            committed_tasks, resolved_loops, committed_mems = await self.event_store.commit_proposal_transaction(
                episode_id=proposal_commit.episode_id,
                scene_id=proposal_commit.scene_id,
                task_proposals=proposal_commit.outcome.task_proposals,
                resolve_open_loop_ids=proposal_commit.outcome.resolve_open_loop_ids,
                memory_proposals=proposal_commit.outcome.memory_proposals,
                deliveries=deliveries,
                acknowledgements=acknowledgements,
                scene_commit=scene_commit,
                job_proposals=outcome.job_proposals,
                job_messages=outcome.message_proposals,
                origin_mode=curr_origin,
                bot_actor_id=self.bot_actor_id,
                through_rowid=(scene_commit["event"].metadata["through_event_rowid"]
                               if scene_commit else current_scene_state.last_observed_event_rowid),
            )
            if resolved_loops and self.metrics:
                self.metrics.inc_social("openloops_resolved", len(resolved_loops))
            committed_proposal = CommittedProposal(
                episode_id=proposal_commit.episode_id,
                scene_id=proposal_commit.scene_id,
                committed_tasks=committed_tasks,
                resolved_loop_ids=resolved_loops,
                committed_memories=committed_mems,
                outcome=proposal_commit.outcome
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

        # 3. External Side-Effect Distribution (Scheduler): only register in heap after DB commit succeeds!
        if self.scheduler and committed_tasks:
            for item in committed_tasks:
                self.scheduler.schedule_task(item)
        if self.scheduler and (outcome.task_proposals or outcome.job_proposals):
            await self.scheduler._sync_from_db()
            self.scheduler._wake_event.set()

        # A no-message commit changes state without enqueueing an expression.
        if outcome.disposition == FinalDisposition.SILENCE:
            reason = (f"Operator controls committed: {outcome.decision_reason}" if operator_control
                      else f"Model selected SILENCE: {outcome.decision_reason}")
            return GateDecision(
                FinalDisposition.SILENCE,
                reason,
                committed_proposal=committed_proposal
            )

        # 5. External Side-Effect Distribution (ActionQueue): enqueue message actions with dependent Open Loops
        actions_count = 0
        now = self.event_store.clock()
        task_rows = {row['id']: row for row in await self.event_store.scene_tasks(current_scene_state.scene_id)} if deliveries or outcome.job_proposals else {}
        for index, msg in enumerate(outcome.message_proposals):
            associated_loop = None
            if mailbox.output_kind == 'chat' and msg.expect_reply and msg.reply_target:
                associated_loop = {
                    "id": f"loop_{uuid.uuid4().hex[:10]}",
                    "scene_id": current_scene_state.scene_id,
                    "target_actor_id": msg.reply_target,
                    "intent": msg.reply_intent or "general_response",
                    "source_event_id": "", # Will be filled by SceneActor on MESSAGE_SENT
                    "source_stimulus_id": getattr(mailbox, "origin_stimulus_id", None),
                    "status": "active",
                    "created_at": now,
                    "expires_at": now + self.open_loop_ttl_seconds,
                }

            action_type = (
                ActionType.SEND_PRIVATE_MESSAGE
                if current_scene_state.scene_id.startswith("private:")
                else ActionType.SEND_GROUP_MESSAGE
            )
            action_origin = "shadow" if (curr_origin == "shadow" or getattr(mailbox, "origin_mode", "live") == "shadow") else "live"
            if msg.fulfils_task_id and task_rows[msg.fulfils_task_id]["origin_mode"] == "shadow":
                action_origin = "shadow"
            job_id, job_revision = msg.job_id, msg.job_revision
            if msg.task_ref:
                # Proposal refs repeat across turns; the transaction binds this unique action ID.
                task = next((row for row in task_rows.values() if row["payload"].get("ack_action_id") == action_ids[index]), None)
                if task and task["payload"].get("kind") == "agent_job":
                    job = await self.event_store.get_job(task["id"], current_scene_state.scene_id)
                    job_id, job_revision = job["id"], job["revision"]
            segments = list(msg.segments)
            if mailbox.output_kind == 'announcement' and self.scene_policy.scene(current_scene_state.scene_id).mention_all:
                segments.insert(0, AllMentionSegment())
            action = ActionItem(
                source_started_at=mailbox.source_started_at,
                id=action_ids[index],
                fulfils_task_id=msg.fulfils_task_id,
                action_type=action_type,
                scene_id=current_scene_state.scene_id,
                segments=segments,
                output_kind=mailbox.output_kind,
                requester_qq_uid=mailbox.requester_qq_uid,
                origin_event_id=mailbox.origin_stimulus_id,
                command_id=mailbox.command_id,
                announcement_member=mailbox.announcement_member,
                batch_id=proposal_commit.episode_id,
                batch_index=index, batch_size=len(outcome.message_proposals),
                reply_to=msg.reply_to,
                response_actor_ids=response_actors[index],
                associated_open_loop=associated_loop,
                origin_mode=action_origin,
                job_id=job_id, job_revision=job_revision,
            )
            self.action_queue.enqueue(action)
            actions_count += 1

        decision = GateDecision(
            FinalDisposition.ACTION,
            f"Approved {actions_count} message proposals",
            actions_enqueued=actions_count,
            committed_proposal=committed_proposal
        )
        decision.action_ids = action_ids
        return decision
