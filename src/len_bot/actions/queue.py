import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable
from len_bot.actions.models import ActionItem, ActionType, DeliveryResult, DeliveryStatus
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)

class ActionQueue:
    def __init__(
        self,
        event_store: EventStore,
        send_adapter: Optional[Callable[[ActionItem], Awaitable[DeliveryResult]]] = None,
        on_action_event: Optional[Callable[[Event], Awaitable[None]]] = None,
        bot_actor_id: str = "system:action_queue",
        action_interceptor: Optional[Callable[[ActionItem], Awaitable[Optional[ActionItem]]]] = None,
        shadow_probe: Optional[Callable[[], bool]] = None,
        shadow_recorder: Optional[Callable[[ActionItem], Awaitable[None]]] = None
    ):
        self.event_store = event_store
        self.send_adapter = send_adapter
        self.on_action_event = on_action_event
        self.bot_actor_id = bot_actor_id
        self.action_interceptor = action_interceptor
        # ADR-0023 Shadow Mode: shadow_probe() True → record instead of send.
        self.shadow_probe = shadow_probe
        self.shadow_recorder = shadow_recorder
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

    async def _emit(self, event: Event, associated_open_loop=None) -> None:
        if self.on_action_event:
            await self.on_action_event(event)
        else:
            await self.event_store.commit_scene_event(
                event=event, scene_state_data={}, associated_open_loop=associated_open_loop,
            )

    async def _process(self, action: ActionItem) -> None:
        original = action
        failure_reason = ""
        if self.action_interceptor:
            try:
                action = await self.action_interceptor(action)
                if action is None:
                    failure_reason = "发送被安全检查阻止"
            except Exception as error:
                failure_reason = f"发送检查失败：{error}"
                action = None
        if action is None:
            await self._emit(Event(
                event_type=EventType.MESSAGE_SEND_FAILED, scene_id=original.scene_id,
                actor_id=self.bot_actor_id, timestamp=self.event_store.clock(), payload={
                    "action_id": original.id, "fulfils_task_id": original.fulfils_task_id,
                    "error": failure_reason, "delivery_unknown": False,
                },
            ))
            return

        shadow = bool(self.shadow_probe and self.shadow_probe()) or action.origin_mode == "shadow"
        if shadow:
            if self.shadow_recorder:
                await self.shadow_recorder(action)
            await self._emit(Event(
                event_type=EventType.ACTION_SHADOWED, scene_id=action.scene_id,
                actor_id=self.bot_actor_id, payload={
                    "action_id": action.id, "fulfils_task_id": action.fulfils_task_id,
                    "content": action.content, "origin_mode": "shadow",
                },
            ))
            return

        try:
            delivery = await self.send_adapter(action) if self.send_adapter else DeliveryResult(
                status=DeliveryStatus.NOT_SENT, transport="none", error="未配置发送适配器",
            )
        except Exception as error:
            delivery = DeliveryResult(status=DeliveryStatus.UNKNOWN, transport="adapter",
                                      error_code=type(error).__name__, error="发送适配器异常，结果不确定")
        success = delivery.status == DeliveryStatus.SENT
        event = Event(
            event_type=EventType.MESSAGE_SENT if success else EventType.MESSAGE_SEND_FAILED,
            scene_id=action.scene_id, actor_id=self.bot_actor_id, timestamp=self.event_store.clock(),
            payload={
                "action_id": action.id, "raw_text": action.content, "content": action.content,
                "reply_to": action.reply_to, "fulfils_task_id": action.fulfils_task_id,
                "delivery_unknown": delivery.status == DeliveryStatus.UNKNOWN,
                "error": delivery.error, "delivery_status": delivery.status.value,
                "transport": delivery.transport, "error_code": delivery.error_code,
                "message_id": delivery.message_id,
                "source_started_at": action.source_started_at,
                "event_to_delivery_ms": round(max(0, self.event_store.clock()-action.source_started_at)*1000)
                    if action.source_started_at is not None else None,
            },
        )
        if success and action.associated_open_loop:
            event.metadata["associated_open_loop"] = action.associated_open_loop
        await self._emit(event, action.associated_open_loop if success else None)

    async def _worker_loop(self) -> None:
        while self._running:
            action = await self._queue.get()
            try:
                await self._process(action)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed confirmation stays awaiting_delivery and becomes unknown on recovery.
                logger.exception("Action processing failed for %s", action.id)
            finally:
                self._queue.task_done()
