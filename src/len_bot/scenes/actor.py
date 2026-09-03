import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable, Any, Union
from len_bot.events.models import Event, EventType
from len_bot.scenes.models import SceneState
from len_bot.scenes.reducer import SceneReducer
from len_bot.events.store import EventStore
from len_bot.cognition.mailbox import EpisodeMailbox
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
        self._queue: asyncio.Queue[Union[Event, ProposalCommitCommand]] = asyncio.Queue()
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

    async def _process_loop(self) -> None:
        while self._running:
            try:
                item = await self._queue.get()
                if isinstance(item, ProposalCommitCommand):
                    try:
                        decision = await item.runtime_gate.evaluate_and_commit(
                            outcome=item.outcome,
                            mailbox=item.mailbox,
                            current_scene_state=self.state,
                            proposal_commit=item.proposal_commit,
                        )
                        # Invariant A: If outcome proposed social_state_proposal and gate accepted, reduce via STATE_ANNOTATION event
                        if decision.accepted and getattr(item.outcome, "social_state_proposal", None):
                            ssp = item.outcome.social_state_proposal
                            metadata_payload = {}
                            if ssp.topic is not None:
                                metadata_payload["topic"] = ssp.topic
                            if ssp.thread_transition is not None:
                                metadata_payload["thread_transition"] = ssp.thread_transition.value if hasattr(ssp.thread_transition, "value") else str(ssp.thread_transition)
                            if metadata_payload:
                                anno_event = Event(
                                    event_type=EventType.STATE_ANNOTATION,
                                    scene_id=self.scene_id,
                                    actor_id="system:cognition",
                                    timestamp=time.time(),
                                    metadata={"social_state": metadata_payload}
                                )
                                candidate_state = SceneReducer.reduce(self.state, anno_event, self.bot_actor_id)
                                await self.event_store.commit_scene_event(
                                    event=anno_event,
                                    scene_state_data=candidate_state.model_dump()
                                )
                                self.state = candidate_state

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
                # 1. Pure functional state reduction to candidate state (never mutates self.state)
                candidate_state = SceneReducer.reduce(self.state, event, self.bot_actor_id)

                # 2. Check if TASK_DUE event with a task_id
                task_id_to_trigger = None
                if event.event_type.value == "TASK_DUE":
                    task_id_to_trigger = event.payload.get("task_id")

                associated_open_loop = event.metadata.get("associated_open_loop")

                # 3. P0-1: Atomically persist Event, FTS, Task triggered status, OpenLoop, and SceneState
                # Persist first!
                await self.event_store.commit_scene_event(
                    event=event,
                    scene_state_data=candidate_state.model_dump(),
                    task_id_to_trigger=task_id_to_trigger,
                    associated_open_loop=associated_open_loop
                )

                # 4. Publish state ONLY after database commit succeeds!
                self.state = candidate_state

                # 5. Route to active Episode Mailbox if present (Steering / Interim tracking)
                if self._active_mailbox:
                    self._active_mailbox.post(event)

                # 6. Notify downstream (StimulusBuilder)
                if self.on_state_updated:
                    await self.on_state_updated(self.state, event)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error processing event in SceneActor %s (in-memory state rolled back/untouched): %s", self.scene_id, e)
                self._queue.task_done()
