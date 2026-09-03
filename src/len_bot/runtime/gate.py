import logging
import uuid
import time
from typing import Optional, Any
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType
from len_bot.scenes.models import SceneState
from len_bot.actions.models import ActionItem, ActionType
from len_bot.actions.queue import ActionQueue
from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)

class GateDecision:
    def __init__(self, disposition: FinalDisposition, reason: str, actions_enqueued: int = 0):
        self.disposition = disposition
        self.reason = reason
        self.actions_enqueued = actions_enqueued

class RuntimeGate:
    def __init__(
        self,
        event_store: EventStore,
        action_queue: ActionQueue,
        scheduler: Optional[Any] = None,
        memory_gate: Optional[Any] = None
    ):
        self.event_store = event_store
        self.action_queue = action_queue
        self.scheduler = scheduler
        self.memory_gate = memory_gate

    async def evaluate_and_commit(
        self,
        outcome: EpisodeOutcome,
        mailbox: EpisodeMailbox,
        current_scene_state: SceneState
    ) -> GateDecision:
        # 1. Freshness Validation (Serialized through SceneActor single-writer queue)
        if mailbox.is_cancelled():
            reason = mailbox.cancellation_reason() or "Episode cancelled by steering"
            logger.info("Gate rejected response due to cancellation: %s", reason)
            return GateDecision(FinalDisposition.SILENCE, f"Gate rejected stale response: {reason}")

        if mailbox.has_follow_up():
            logger.info("Gate rejected response due to pending follow-up superseding this outcome")
            return GateDecision(FinalDisposition.SILENCE, "Gate rejected stale response: pending follow-up supersedes this outcome")

        interim_events = mailbox.get_interim_events()
        cancel_keywords = ["不用了", "不用查了", "算了", "闭嘴", "别发了", "取消", "停"]
        for ie in interim_events:
            if any(ck in ie.raw_text for ck in cancel_keywords):
                logger.info("Gate rejected response due to interim cancellation: %s", ie.raw_text)
                return GateDecision(FinalDisposition.SILENCE, f"Gate rejected due to interim cancellation: {ie.raw_text}")

        # 2. Authoritative Database Commit (Tasks, Open Loops, Memories)
        # Only executed if freshness is 100% intact!
        committed_tasks = await self._commit_independent_state(outcome, current_scene_state.scene_id)

        # 3. External Side-Effect Distribution (Scheduler): only register in heap after DB commit succeeds!
        if self.scheduler and committed_tasks:
            for item in committed_tasks:
                self.scheduler.schedule_task(item)

        # 4. Check if model explicitly chose SILENCE
        if outcome.disposition == FinalDisposition.SILENCE:
            return GateDecision(FinalDisposition.SILENCE, f"Model selected SILENCE: {outcome.thought}")

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
            actions_enqueued=actions_count
        )

    async def _commit_independent_state(self, outcome: EpisodeOutcome, scene_id: str) -> list[Any]:
        from len_bot.scheduler.models import TaskItem, TaskStatus
        now = time.time()
        committed_tasks: list[TaskItem] = []

        # 1. Commit independent tasks via unified event_store write authority
        for tp in outcome.task_proposals:
            task_id = f"task_{uuid.uuid4().hex[:10]}"
            task_data = {
                "id": task_id,
                "scene_id": scene_id,
                "description": tp.description,
                "due_at": now + tp.delay_seconds,
                "status": "pending",
                "source_event_id": "episode",
                "payload": tp.payload,
                "created_at": now
            }
            await self.event_store.create_task(task_data)

            item = TaskItem(
                id=task_data["id"],
                scene_id=task_data["scene_id"],
                description=task_data["description"],
                due_at=task_data["due_at"],
                status=TaskStatus.PENDING,
                payload=task_data["payload"],
                source_event_id=task_data["source_event_id"],
                created_at=task_data["created_at"]
            )
            committed_tasks.append(item)

        # 2. Resolve open loops if specified
        for loop_id in outcome.resolve_open_loop_ids:
            await self.event_store.save_open_loop({
                "id": loop_id,
                "scene_id": scene_id,
                "target_actor_id": "",
                "intent": "",
                "source_event_id": "",
                "status": "resolved",
                "created_at": now,
                "expires_at": now
            })

        # 3. Commit memory proposals via MemoryGate (ADR-0011 & P0.3)
        if self.memory_gate and outcome.memory_proposals:
            for mp in outcome.memory_proposals:
                # P0.3: Enforce that memory scope is strictly injected and fixed to current scene
                mp.scope = scene_id
                await self.memory_gate.commit_proposal(mp)

        return committed_tasks
