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

    def attach_mailbox(self, mailbox: EpisodeMailbox) -> None:
        self._active_mailbox = mailbox

    def detach_mailbox(self, mailbox: EpisodeMailbox) -> None:
        if self._active_mailbox and self._active_mailbox.episode_id == mailbox.episode_id:
            self._active_mailbox = None

    async def _process_loop(self) -> None:
        while self._running:
            try:
                event = await self._queue.get()
                # 1. Single-writer state reduction (synchronous & zero locks)
                self.state = SceneReducer.reduce(self.state, event, self.bot_actor_id)
                
                # 2. Persist materialized state update in SQLite
                await self.event_store.save_scene_state(
                    self.scene_id,
                    self.state.version,
                    self.state.model_dump()
                )

                # 3. Route to active Episode Mailbox if present (Steering)
                if self._active_mailbox:
                    self._active_mailbox.post(event)

                # 4. Notify downstream (e.g. StimulusBuilder)
                if self.on_state_updated:
                    await self.on_state_updated(self.state, event)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error processing event in SceneActor %s: %s", self.scene_id, e)
