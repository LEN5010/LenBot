import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable
from len_bot.actions.models import ActionItem, ActionType
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)

class ActionQueue:
    def __init__(
        self,
        event_store: EventStore,
        send_adapter: Optional[Callable[[ActionItem], Awaitable[bool]]] = None,
        on_action_event: Optional[Callable[[Event], Awaitable[None]]] = None,
        bot_actor_id: str = "system:action_queue"
    ):
        self.event_store = event_store
        self.send_adapter = send_adapter
        self.on_action_event = on_action_event
        self.bot_actor_id = bot_actor_id
        self._queue: asyncio.Queue[ActionItem] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def enqueue(self, action: ActionItem) -> None:
        self._queue.put_nowait(action)

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                action = await self._queue.get()
                success = True
                if self.send_adapter:
                    try:
                        success = await self.send_adapter(action)
                    except Exception as e:
                        logger.error("Failed sending action via adapter: %s", e)
                        success = False

                now = time.time()
                if success:
                    # 1. Emit MESSAGE_SENT Event with canonical payload and actor_id
                    sent_event = Event(
                        event_type=EventType.MESSAGE_SENT,
                        scene_id=action.scene_id,
                        actor_id=self.bot_actor_id,
                        timestamp=now,
                        payload={
                            "action_id": action.id,
                            "raw_text": action.content,
                            "content": action.content,
                            "reply_to": action.reply_to
                        }
                    )

                    # 2. Item 3: Attach associated Open Loop to sent_event metadata for atomic commit in SceneActor
                    if action.associated_open_loop:
                        sent_event.metadata["associated_open_loop"] = action.associated_open_loop

                    # 3. Route to single commit authority (SceneActor)
                    if self.on_action_event:
                        await self.on_action_event(sent_event)
                    else:
                        await self.event_store.commit_scene_event(
                            event=sent_event,
                            scene_state_data={},
                            associated_open_loop=action.associated_open_loop
                        )
                else:
                    # Emit MESSAGE_SEND_FAILED Event
                    fail_event = Event(
                        event_type=EventType.MESSAGE_SEND_FAILED,
                        scene_id=action.scene_id,
                        actor_id=self.bot_actor_id,
                        timestamp=now,
                        payload={"action_id": action.id, "content": action.content, "raw_text": action.content}
                    )
                    if self.on_action_event:
                        await self.on_action_event(fail_event)
                    else:
                        await self.event_store.append_event(fail_event)

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in ActionQueue worker: %s", e)
