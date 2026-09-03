import logging
import uuid
import time
from dataclasses import dataclass, field
from typing import Optional, Any
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType
from len_bot.scenes.models import SceneState
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.events.store import EventStore
from len_bot.scheduler.models import TaskItem
from len_bot.memory.models import MemoryItem

logger = logging.getLogger(__name__)

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

class RuntimeGate:
    def __init__(
        self,
        event_store: EventStore,
        action_queue: ActionQueue,
        scheduler: Optional[Any] = None,
        memory_gate: Optional[Any] = None,
        ambient_store: Optional[Any] = None,
        metrics: Optional[Any] = None
    ):
        self.event_store = event_store
        self.action_queue = action_queue
        self.scheduler = scheduler
        self.memory_gate = memory_gate
        self.ambient_store = ambient_store
        self.metrics = metrics

    async def evaluate_and_commit(
        self,
        outcome: EpisodeOutcome,
        mailbox: EpisodeMailbox,
        current_scene_state: SceneState,
        proposal_commit: Optional[ProposalCommit] = None
    ) -> GateDecision:
        if proposal_commit is None:
            proposal_commit = ProposalCommit(
                episode_id=mailbox.episode_id,
                scene_id=current_scene_state.scene_id,
                base_scene_version=mailbox.base_scene_version,
                outcome=outcome,
                mailbox=mailbox
            )

        # 1. Freshness Validation (Serialized through SceneActor single-writer queue)
        if mailbox.is_cancelled():
            reason = mailbox.cancellation_reason() or "Episode cancelled by steering"
            logger.info("Gate rejected response due to cancellation: %s", reason)
            if self.metrics:
                self.metrics.inc_social("cancellations_honored")
            return GateDecision(FinalDisposition.SILENCE, f"Gate rejected stale response: {reason}", accepted=False)

        if mailbox.has_follow_up():
            logger.info("Gate rejected response due to pending follow-up superseding this outcome")
            return GateDecision(FinalDisposition.SILENCE, "Gate rejected stale response: pending follow-up supersedes this outcome", accepted=False)

        interim_events = mailbox.get_interim_events()
        cancel_keywords = ["不用了", "不用查了", "算了", "闭嘴", "别发了", "取消", "停"]
        for ie in interim_events:
            if any(ck in ie.raw_text for ck in cancel_keywords):
                logger.info("Gate rejected response due to interim cancellation: %s", ie.raw_text)
                if self.metrics:
                    self.metrics.inc_social("cancellations_honored")
                return GateDecision(FinalDisposition.SILENCE, f"Gate rejected due to interim cancellation: {ie.raw_text}", accepted=False)

        # ADR-0026 / §8.2: Gate last window check: if unread interim events arrived, reject as stale
        if mailbox.has_unseen_interim():
            logger.info("Gate rejected response due to unread interim events (semantic staleness)")
            if self.metrics:
                self.metrics.inc_social("stale_outcomes_rejected")
            return GateDecision(
                FinalDisposition.SILENCE,
                "Gate rejected stale response: unread interim events arrived during deliberation",
                accepted=False
            )

        # 2. Authoritative Database Commit (Tasks, Open Loops, Memories) - All-or-Nothing Atomic Transaction
        try:
            committed_tasks, resolved_loops, committed_mems = await self.event_store.commit_proposal_transaction(
                episode_id=proposal_commit.episode_id,
                scene_id=proposal_commit.scene_id,
                task_proposals=proposal_commit.outcome.task_proposals,
                resolve_open_loop_ids=proposal_commit.outcome.resolve_open_loop_ids,
                memory_proposals=proposal_commit.outcome.memory_proposals
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

        # 3b. External Side-Effect Distribution (Ambient Store, ADR-0018): retained items are
        # non-durable soft state, so they are written AFTER the durable transaction — same tier
        # as scheduler heap registration, never inside it.
        if self.ambient_store and proposal_commit.outcome.retained_item_proposals:
            for rp in proposal_commit.outcome.retained_item_proposals:
                self.ambient_store.retain(
                    scope=proposal_commit.scene_id,
                    source="cognition",
                    topic=rp.topic,
                    summary=rp.summary,
                    salience=rp.salience,
                    evidence_event_id=rp.source_event_id or ""
                )

        # 4. Check if model explicitly chose SILENCE
        if outcome.disposition == FinalDisposition.SILENCE:
            return GateDecision(
                FinalDisposition.SILENCE,
                f"Model selected SILENCE: {outcome.decision_reason}",
                committed_proposal=committed_proposal
            )

        # 5. External Side-Effect Distribution (ActionQueue): enqueue message actions with dependent Open Loops
        actions_count = 0
        now = time.time()
        for msg in outcome.message_proposals:
            associated_loop = None
            if msg.expect_reply and msg.reply_target:
                associated_loop = {
                    "id": f"loop_{uuid.uuid4().hex[:10]}",
                    "scene_id": current_scene_state.scene_id,
                    "target_actor_id": msg.reply_target,
                    "intent": msg.reply_intent or "general_response",
                    "source_event_id": "", # Will be filled by SceneActor on MESSAGE_SENT
                    "status": "active",
                    "created_at": now,
                    "expires_at": now + 86400.0 # 24h TTL
                }

            action_type = (
                ActionType.SEND_PRIVATE_MESSAGE
                if current_scene_state.scene_id.startswith("private:")
                else ActionType.SEND_GROUP_MESSAGE
            )
            action = ActionItem(
                action_type=action_type,
                scene_id=current_scene_state.scene_id,
                content=msg.content,
                reply_to=msg.reply_to,
                associated_open_loop=associated_loop
            )
            self.action_queue.enqueue(action)
            actions_count += 1

        return GateDecision(
            FinalDisposition.ACTION,
            f"Approved {actions_count} message proposals",
            actions_enqueued=actions_count,
            committed_proposal=committed_proposal
        )
