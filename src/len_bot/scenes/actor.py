import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable, Any, Union
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState
from len_bot.scenes.reducer import SceneReducer
from len_bot.events.store import EventStore
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.session import (
    GroupAgentSession,
    GroupAgentSessionReducer,
    SocialCognitionResult,
)
from len_bot.runtime.gate import ProposalCommit

logger = logging.getLogger(__name__)

class ProposalCommitCommand:
    def __init__(
        self,
        episode_id: str,
        scene_id: str,
        base_scene_version: int,
        outcome: Any,
        mailbox: EpisodeMailbox,
        runtime_gate: Any,
    ):
        self.proposal_commit = ProposalCommit(
            episode_id=episode_id,
            scene_id=scene_id,
            base_scene_version=base_scene_version,
            outcome=outcome,
            mailbox=mailbox,
        )
        self.episode_id = episode_id
        self.scene_id = scene_id
        self.base_scene_version = base_scene_version
        self.outcome = outcome
        self.mailbox = mailbox
        self.runtime_gate = runtime_gate
        self.future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()


class SocialCognitionCommitCommand:
    def __init__(
        self,
        result: SocialCognitionResult,
        through_event_rowid: int,
        source_event_ids: list[str],
        mode: str,
        gate_context: tuple | None = None,
        social_revision: int | None = None,
    ):
        self.gate_context = gate_context
        self.social_revision = social_revision
        self.result = result
        self.through_event_rowid = through_event_rowid
        self.source_event_ids = source_event_ids
        self.mode = mode
        self.future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()


class SceneActor:
    def __init__(
        self,
        scene_id: str,
        bot_actor_id: str,
        event_store: EventStore,
        on_state_updated: Optional[Callable[[SceneState, Event], Awaitable[None]]] = None
    ):
        self.scene_id = scene_id
        self.bot_actor_id = bot_actor_id
        self.event_store = event_store
        self.on_state_updated = on_state_updated
        self.state: Optional[SceneState] = None
        self.group_session: Optional[GroupAgentSession] = None
        self._queue: asyncio.Queue[
            Union[Event, ProposalCommitCommand, SocialCognitionCommitCommand]
        ] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._active_mailbox: Optional[EpisodeMailbox] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        # Load persisted materialized state from SQLite if exists
        saved = await self.event_store.load_scene_state(self.scene_id)
        if saved:
            self.state = SceneState(**saved)
        else:
            self.state = SceneState(scene_id=self.scene_id, version=0)

        saved_session = await self.event_store.load_group_agent_session(self.scene_id)
        if saved_session:
            self.group_session = GroupAgentSession(**saved_session)
        else:
            self.group_session = GroupAgentSession(scene_id=self.scene_id)

        self._running = True
        self._worker_task = asyncio.create_task(self._process_loop())

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def post_event(self, event: Event) -> None:
        self._queue.put_nowait(event)

    def acquire_episode_lease(self, episode_id: str, mailbox: EpisodeMailbox) -> bool:
        """P0.2: Enforce single active cognitive episode per scene."""
        if self._active_mailbox is not None:
            logger.warning(
                "Scene %s already has active episode %s; rejecting new episode %s",
                self.scene_id, self._active_mailbox.episode_id, episode_id
            )
            return False
        self._active_mailbox = mailbox
        return True

    def release_episode_lease(self, episode_id: str) -> None:
        if self._active_mailbox and self._active_mailbox.episode_id == episode_id:
            self._active_mailbox = None

    def attach_mailbox(self, mailbox: EpisodeMailbox) -> bool:
        return self.acquire_episode_lease(mailbox.episode_id, mailbox)

    def detach_mailbox(self, mailbox: EpisodeMailbox) -> None:
        self.release_episode_lease(mailbox.episode_id)

    def has_active_episode(self) -> bool:
        return self._active_mailbox is not None

    async def submit_proposal(
        self,
        episode_id: str,
        outcome: Any,
        mailbox: EpisodeMailbox,
        runtime_gate: Any,
    ) -> Any:
        """P0-2: Submits an episode outcome for authoritative serialization through SceneActor."""
        cmd = ProposalCommitCommand(
            episode_id=episode_id,
            scene_id=self.scene_id,
            base_scene_version=mailbox.base_scene_version,
            outcome=outcome,
            mailbox=mailbox,
            runtime_gate=runtime_gate,
        )
        await self._queue.put(cmd)
        return await cmd.future

    async def submit_social_cognition(
        self,
        result: SocialCognitionResult,
        through_event_rowid: int,
        source_event_ids: list[str],
        mode: str = "shadow",
    ) -> bool:
        cmd = SocialCognitionCommitCommand(
            result=result,
            through_event_rowid=through_event_rowid,
            source_event_ids=source_event_ids,
            mode=mode,
        )
        await self._queue.put(cmd)
        return await cmd.future

    async def _process_loop(self) -> None:
        while self._running:
            try:
                item = await self._queue.get()
                if isinstance(item, SocialCognitionCommitCommand):
                    try:
                        accepted = await self._commit_social_cognition(item)
                        if not item.future.done():
                            item.future.set_result(accepted)
                    except Exception as error:
                        logger.exception(
                            "Error committing social cognition in SceneActor %s: %s",
                            self.scene_id,
                            error,
                        )
                        if not item.future.done():
                            item.future.set_exception(error)
                    finally:
                        self._queue.task_done()
                    continue

                if isinstance(item, ProposalCommitCommand):
                    try:
                        decision = await item.runtime_gate.evaluate_and_commit(
                            outcome=item.outcome,
                            mailbox=item.mailbox,
                            current_scene_state=self.state,
                            proposal_commit=item.proposal_commit,
                        )
                        if not item.future.done():
                            item.future.set_result(decision)
                    except Exception as e:
                        logger.exception("Error evaluating proposal in SceneActor %s: %s", self.scene_id, e)
                        if not item.future.done():
                            item.future.set_exception(e)
                    finally:
                        self._queue.task_done()
                    continue

                event: Event = item
                if await self.event_store.event_exists(event.id, self.scene_id):
                    self._queue.task_done()
                    continue
                if event.event_type == EventType.TASK_DUE and event.payload.get("task_id"):
                    event.metadata["obsolete_task_wake"] = not await self.event_store.task_due_is_current(event)
                if event.event_type in {EventType.AGENT_JOB_FINISHED, EventType.AGENT_JOB_PROGRESS}:
                    job = await self.event_store.get_job(event.payload.get("job_id"), self.scene_id)
                    valid_states = {"result_ready"} if event.event_type == EventType.AGENT_JOB_FINISHED else {"processing", "result_ready"}
                    event.metadata["obsolete_job_result"] = not job or job["revision"] != event.payload.get("job_revision") or job["status"] not in valid_states
                if event.event_type == EventType.REFLECTION_RECORDED:
                    event.metadata["reflection_stale"] = event.payload.get("social_revision") != self.group_session.social_revision
                    event.metadata["needs_review"] = bool(event.payload.get("review_items")) or (
                        event.metadata["reflection_stale"]
                        and any(event.payload.get(key) for key in ("patch", "episode_summary", "memory_receipts"))
                    )
                # 1. Pure functional state reduction to candidate state (never mutates self.state)
                candidate_state = SceneReducer.reduce(self.state, event, self.bot_actor_id)
                candidate_session = GroupAgentSessionReducer.reduce(
                    self.group_session, event, self.bot_actor_id
                )

                # 2. Check if TASK_DUE event with a task_id
                task_id_to_trigger = None
                if event.event_type.value == "TASK_DUE":
                    task_id_to_trigger = event.payload.get("task_id")

                associated_open_loop = event.metadata.get("associated_open_loop")
                background_bookkeeping = (
                    event.event_type in {EventType.AGENT_JOB_CONTROL, EventType.AGENT_JOB_CHECKPOINT}
                    or event.event_type == EventType.TOOL_OBSERVATION_RECORDED and event.metadata.get("background_work")
                    or event.event_type == EventType.TASK_DUE and event.payload.get("payload", {}).get("kind") == "agent_job"
                )

                # 3. P0-1: Atomically persist Event, FTS, Task triggered status, OpenLoop, and SceneState
                # Persist first!
                event_rowid = await self.event_store.commit_scene_event(
                    event=event,
                    scene_state_data=candidate_state.model_dump(),
                    task_id_to_trigger=task_id_to_trigger,
                    associated_open_loop=associated_open_loop,
                    group_session_data=candidate_session.model_dump(),
                    advance_session_observation=not background_bookkeeping,
                )

                # 4. Publish state ONLY after database commit succeeds!
                if not background_bookkeeping:
                    candidate_session.last_observed_event_rowid = event_rowid
                self.state = candidate_state
                self.group_session = candidate_session

                # 5. Route to active Episode Mailbox if present (Steering / Interim tracking)
                if self._active_mailbox:
                    self._active_mailbox.post(event)

                # 6. Notify downstream (BurstAssembler)
                if self.on_state_updated:
                    await self.on_state_updated(self.state, event)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error processing event in SceneActor %s (in-memory state rolled back/untouched): %s", self.scene_id, e)
                self._queue.task_done()

    async def commit_cognitive_turn(self, result, through_event_rowid, source_event_ids,
                                    mode, episode_id, mailbox, runtime_gate, social_revision=None):
        cmd = SocialCognitionCommitCommand(
            result, through_event_rowid, source_event_ids, mode,
            (episode_id, mailbox, runtime_gate),
            social_revision=social_revision,
        )
        self._queue.put_nowait(cmd)
        return await cmd.future

    async def _commit_social_cognition(self, item: SocialCognitionCommitCommand) -> bool:
        if item.social_revision is not None and self.group_session.social_revision != item.social_revision:
            return False
        bounded_chat = item.social_revision is not None and not item.result.requires_fresh_input()
        if item.through_event_rowid > self.group_session.last_observed_event_rowid:
            return False
        if item.through_event_rowid < self.group_session.last_cognized_event_rowid:
            return False
        if not bounded_chat and self.group_session.last_observed_event_rowid != item.through_event_rowid:
            return False

        referenced_event_ids = set(item.source_event_ids)
        referenced_event_ids.update(item.result.perception.world_patch.source_event_ids)
        if item.result.self_state:
            referenced_event_ids.update(item.result.self_state.source_event_ids)
        for thread in item.result.perception.world_patch.open_threads:
            referenced_event_ids.update(thread.source_event_ids)
        for expectation in item.result.perception.world_patch.latent_expectations:
            referenced_event_ids.update(expectation.source_event_ids)
        for person in item.result.perception.person_updates:
            referenced_event_ids.update(person.source_event_ids)
        for relationship in item.result.perception.relationship_updates:
            referenced_event_ids.update(relationship.source_event_ids)
        for retained in item.result.retained_attention:
            referenced_event_ids.update(retained.source_event_ids)
        if item.result.future_attention:
            referenced_event_ids.update(item.result.future_attention.source_event_ids)
        for memory in item.result.memory_candidates:
            referenced_event_ids.update(memory.evidence)
        for job in item.result.job_proposals:
            referenced_event_ids.update(job.source_event_ids)
        memory_ids = {mid for update in [*item.result.perception.person_updates,
                                        *item.result.perception.relationship_updates]
                      for mid in update.memory_ids_add}
        if memory_ids and not await self.event_store.memory_references_readable(memory_ids, self.scene_id):
            raise ValueError("Working memory references outside readable scope")
        if not await self.event_store.references_belong_to_scene(
            referenced_event_ids,
            self.scene_id,
            item.through_event_rowid,
        ):
            raise ValueError("Social cognition references evidence outside the scene scope or observation cutoff")

        cognition_event = Event(
            event_type=EventType.SOCIAL_COGNITION_RECORDED,
            scene_id=self.scene_id,
            actor_id="system:social_core",
            timestamp=self.event_store.clock(),
            payload={
                "source_event_ids": item.source_event_ids,
                "result": item.result.model_dump(mode="json"),
            },
            metadata={
                "through_event_rowid": item.through_event_rowid,
                "mode": item.mode,
            },
        )
        candidate_state = SceneReducer.reduce(self.state, cognition_event, self.bot_actor_id)
        candidate_session = GroupAgentSessionReducer.apply_cognition(
            self.group_session,
            item.result,
            item.through_event_rowid,
            now=self.event_store.clock(),
        )
        scene_commit = dict(
            event=cognition_event,
            scene_state_data=candidate_state.model_dump(),
            group_session_data=candidate_session.model_dump(),
            advance_session_observation=False,
        )
        if item.gate_context is not None:
            episode_id, mailbox, runtime_gate = item.gate_context
            if self._active_mailbox is not mailbox:
                return False
            decision = await runtime_gate.evaluate_and_commit(
                outcome=item.result.to_episode_outcome(self.scene_id, item.through_event_rowid, self.event_store.clock()),
                mailbox=mailbox, current_scene_state=self.state, scene_commit=scene_commit,
                bounded_chat=bounded_chat,
            )
            if decision.accepted:
                self.state = candidate_state
                self.group_session = GroupAgentSession.model_validate(scene_commit["group_session_data"])
            return decision
        await self.event_store.commit_scene_event(**scene_commit)
        self.state = candidate_state
        self.group_session = candidate_session
        return True
