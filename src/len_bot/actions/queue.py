import asyncio
import logging
import time
from typing import Optional, Callable, Awaitable
from len_bot.actions.models import ActionItem, DeliveryResult, DeliveryStatus
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)


class ActionQueue:
    """Per-scene ordering; failures stop only the same request's dependent messages."""
    def __init__(self, event_store: EventStore,
                 send_adapter: Optional[Callable[[ActionItem], Awaitable[DeliveryResult]]] = None,
                 on_action_event=None, bot_actor_id="system:action_queue", prepare_action=None,
                 shadow_probe=None, shadow_recorder=None, *, max_concurrent: int):
        self.event_store, self.send_adapter = event_store, send_adapter
        self.on_action_event, self.bot_actor_id = on_action_event, bot_actor_id
        self.prepare_action = prepare_action
        self.shadow_probe, self.shadow_recorder = shadow_probe, shadow_recorder
        self._queue: asyncio.Queue[ActionItem] = asyncio.Queue()
        self._worker_task = None
        self._running = False
        self._scene_queues = {}
        self._scene_workers = {}
        self._delivery_slots = asyncio.Semaphore(max_concurrent)
        self._failed_requests: dict[tuple[str, str], set[tuple[str | None, str | None]]] = {}
        self._attempted = set()
        self._enqueued_at = {}
        self.checkpoint = None
        self.simulated = False
        self.pacing = False
        self.sleep = asyncio.sleep
        self.validate_before_send = None

    async def start(self):
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        self._running = False
        tasks = [task for task in [self._worker_task, *self._scene_workers.values()] if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._failed_requests.clear()
        for queue in [self._queue, *self._scene_queues.values()]:
            while not queue.empty():
                action = queue.get_nowait()
                try:
                    await self._reject(action, "队列已停止，未发送", unknown=False)
                finally:
                    queue.task_done()
                    if queue is not self._queue:
                        self._queue.task_done()
        self._scene_workers.clear()
        self._scene_queues.clear()
        self._enqueued_at.clear()

    def enqueue(self, action):
        self._enqueued_at[action.id] = time.monotonic()
        self._queue.put_nowait(action)

    async def _emit(self, event, associated_open_loop=None):
        if self.on_action_event:
            await self.on_action_event(event)
        else:
            await self.event_store.commit_scene_event(event=event, scene_state_data={}, associated_open_loop=associated_open_loop)

    @staticmethod
    def _payload(action):
        return {"action_id": action.id, "raw_text": action.content, "content": action.content,
                "segments": [segment.model_dump() for segment in action.segments],
                "batch_id": action.batch_id, "batch_index": action.batch_index, "batch_size": action.batch_size,
                "job_id": action.job_id, "job_revision": action.job_revision,
                "acknowledges_task_id": action.acknowledges_task_id,
                "operation_ref": action.operation_ref,
                "reply_to": action.reply_to, "fulfils_task_id": action.fulfils_task_id,
                "response_actor_ids": action.response_actor_ids,
                "output_kind": action.output_kind, "requester_qq_uid": action.requester_qq_uid,
                "origin_event_id": action.origin_event_id, "command_id": action.command_id,
                "announcement_member": action.announcement_member,
                "source_started_at": action.source_started_at}

    def _shadow(self, action):
        return (action.origin_mode == "shadow" or action.scene_id.startswith('private:')
                or bool(self.shadow_probe and self.shadow_probe()))

    async def _reject(self, action, reason, *, unknown=False):
        await self._emit(Event(event_type=EventType.MESSAGE_SEND_FAILED, scene_id=action.scene_id,
            actor_id=self.bot_actor_id, timestamp=self.event_store.clock(), metadata={"simulated": self.simulated},
            payload={**self._payload(action), "error": reason, "delivery_unknown": unknown,
                     "delivery_status": "unknown" if unknown else "not_sent"}))
        return False

    async def _record_shadow(self, action):
        if self.shadow_recorder:
            await self.shadow_recorder(action)
        await self._emit(Event(event_type=EventType.ACTION_SHADOWED, scene_id=action.scene_id,
            actor_id=self.bot_actor_id, timestamp=self.event_store.clock(),
            metadata={"simulated": self.simulated},
            payload={**self._payload(action), "origin_mode": "shadow"}))
        return True

    async def _validate(self, action):
        if action.operation_ref:
            await self.event_store.validate_operation_message(action)
        elif action.job_id:
            await self.event_store.validate_job_message(action.scene_id, action.job_id, action.job_revision, bool(action.fulfils_task_id))
        if self.validate_before_send:
            await self.validate_before_send(action)

    @staticmethod
    def _request_key(action):
        return (action.origin_event_id,
                action.job_id or action.fulfils_task_id or action.acknowledges_task_id or action.operation_ref)

    def _request_failed(self, action):
        return self._request_key(action) in self._failed_requests.get((action.scene_id, action.batch_id), ())

    def _remember_failure(self, action):
        if action.batch_id:
            self._failed_requests.setdefault((action.scene_id, action.batch_id), set()).add(self._request_key(action))

    async def _process(self, action):
        if self.checkpoint:
            await self.checkpoint("before_send", {"scene_id": action.scene_id, "action": action.model_dump(mode="json")})
        original = action
        if self._request_failed(action):
            return await self._reject(action, "同一请求与事项的前条消息未确认送达，停止其后续消息")
        try:
            if self.prepare_action:
                action = await self.prepare_action(action)
            await self._validate(action)
        except Exception as error:
            return await self._reject(original, f"发送检查失败：{error}")
        if self._shadow(action):
            return await self._record_shadow(action)
        async with self._delivery_slots:
            try:
                await self._validate(action)
            except Exception as error:
                return await self._reject(action, str(error))
            if self._shadow(action):
                return await self._record_shadow(action)
            send_started = time.monotonic()
            queue_ms = round((send_started-self._enqueued_at.get(action.id, send_started))*1000, 2)
            try:
                if self.send_adapter:
                    self._attempted.add(action.id)
                    delivery = await self.send_adapter(action)
                else:
                    delivery = DeliveryResult(status=DeliveryStatus.NOT_SENT, transport="none", error="未配置发送适配器")
            except Exception as error:
                delivery = DeliveryResult(status=DeliveryStatus.UNKNOWN, transport="adapter", error_code=type(error).__name__, error="发送适配器异常，结果不确定")
        send_ms = round((time.monotonic()-send_started)*1000, 2)
        success = delivery.status == DeliveryStatus.SENT
        event = Event(event_type=EventType.MESSAGE_SENT if success else EventType.MESSAGE_SEND_FAILED,
            scene_id=action.scene_id, actor_id=self.bot_actor_id, timestamp=self.event_store.clock(),
            metadata={"simulated": self.simulated, "media": [{"asset_id": segment.asset_id, "type": "image"} for segment in action.segments if segment.type == "image"]},
            payload={**self._payload(action), "origin_mode": "simulated" if self.simulated else action.origin_mode,
                "delivery_unknown": delivery.status == DeliveryStatus.UNKNOWN, "error": delivery.error,
                "delivery_status": delivery.status.value, "transport": delivery.transport,
                "queue_ms": queue_ms, "send_ms": send_ms,
                "error_code": delivery.error_code, "message_id": delivery.message_id,
                "event_to_delivery_ms": round(max(0, self.event_store.clock()-action.source_started_at)*1000)
                    if action.source_started_at is not None else None})
        if success and action.associated_open_loop:
            event.metadata["associated_open_loop"] = action.associated_open_loop
        await self._emit(event, action.associated_open_loop if success else None)
        self._attempted.discard(action.id)
        if self.checkpoint:
            await self.checkpoint("after_send", {"scene_id": action.scene_id, "event": event.model_dump(mode="json")})
        return success

    async def _worker_loop(self):
        while self._running:
            action = await self._queue.get()
            queue = self._scene_queues.setdefault(action.scene_id, asyncio.Queue())
            queue.put_nowait(action)
            worker = self._scene_workers.get(action.scene_id)
            if worker is None or worker.done():
                worker = asyncio.create_task(self._scene_loop(action.scene_id, queue))
                self._scene_workers[action.scene_id] = worker

    async def _scene_loop(self, scene_id, queue):
        while self._running:
            action = await queue.get()
            try:
                if self.pacing and action.batch_index > 0 and not self._request_failed(action):
                    await self.sleep(min(2.0, max(0.6, len(action.content)/40.0)))
                if not await self._process(action):
                    self._remember_failure(action)
            except asyncio.CancelledError:
                await self._reject(action, "发送队列中断", unknown=action.id in self._attempted)
                raise
            except Exception:
                self._remember_failure(action)
                logger.exception("Action processing failed for %s; unconfirmed delivery requires review", action.id)
            finally:
                self._attempted.discard(action.id)
                self._enqueued_at.pop(action.id, None)
                if action.batch_id and action.batch_index == action.batch_size-1:
                    self._failed_requests.pop((action.scene_id, action.batch_id), None)
                queue.task_done()
                self._queue.task_done()
