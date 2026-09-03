import asyncio
import logging
from typing import Optional, Callable, Awaitable
from len_bot.events.models import Event
from len_bot.scenes.models import SceneState
from len_bot.scenes.reducer import SceneReducer
from len_bot.events.store import EventStore
from len_bot.cognition.mailbox import EpisodeMailbox

logger = logging.getLogger(__name__)

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
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
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

    async def _process_loop(self) -> None:
        while self._running:
            try:
                event = await self._queue.get()
                # 1. Single-writer state reduction (synchronous & zero locks)
                self.state = SceneReducer.reduce(self.state, event, self.bot_actor_id)

                # 2. Check if TASK_DUE event with a task_id
                task_id_to_trigger = None
                if event.event_type.value == "TASK_DUE":
                    task_id_to_trigger = event.payload.get("task_id")

                associated_open_loop = event.metadata.get("associated_open_loop")

                # 3. P0.1, P0.4 & Item 3: Atomically persist Event, FTS, Task triggered status, OpenLoop, and SceneState
                await self.event_store.commit_scene_event(
                    event=event,
                    scene_state_data=self.state.model_dump(),
                    task_id_to_trigger=task_id_to_trigger,
                    associated_open_loop=associated_open_loop
                )

                # 4. Route to active Episode Mailbox if present (Steering / Interim tracking)
                if self._active_mailbox:
                    self._active_mailbox.post(event)

                # 5. Notify downstream (StimulusBuilder)
                if self.on_state_updated:
                    await self.on_state_updated(self.state, event)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error processing event in SceneActor %s: %s", self.scene_id, e)
