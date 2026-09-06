import asyncio
import heapq
import logging
import time
from typing import Optional, Callable, Awaitable, Any
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.scheduler.models import TaskItem, TaskStatus

logger = logging.getLogger(__name__)

class TaskScheduler:
    def __init__(
        self,
        event_store: EventStore,
        emit_event: Callable[[Event], Awaitable[None]],
        sweep_interval: float = 10.0,
        metrics: Optional[Any] = None
    ):
        self.event_store = event_store
        self.emit_event = emit_event
        self.sweep_interval = sweep_interval
        self.metrics = metrics
        self._heap: list[TaskItem] = []
        self._wake_event = asyncio.Event()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._known_task_ids: set[str] = set()
        self.jobs_enabled_probe = lambda: True

    async def start(self) -> None:
        self._running = True
        await self.event_store.recover_task_execution()
        for event in await self.event_store.pending_runtime_events():
            await self.emit_event(event)
        await self._sync_from_db()
        self._worker_task = asyncio.create_task(self._scheduler_loop())
        logger.info("TaskScheduler started with %d pending tasks", len(self._heap))

    async def stop(self) -> None:
        self._running = False
        self._wake_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def schedule_task(self, task: TaskItem) -> None:
        if task.id not in self._known_task_ids:
            self._known_task_ids.add(task.id)
            heapq.heappush(self._heap, task)
            self._wake_event.set()

    async def _sync_from_db(self) -> None:
        now = self.event_store.clock()
        pending = await self.event_store.get_pending_tasks()
        self._heap.clear()
        self._known_task_ids.clear()
        for p in pending:
            task = TaskItem(
                id=p["id"],
                scene_id=p["scene_id"],
                description=p["description"],
                due_at=p["due_at"],
                status=TaskStatus(p["status"]),
                payload=p.get("payload", {}) if isinstance(p.get("payload"), dict) else {},
                source_event_id=p.get("source_event_id", "episode"),
                created_at=p.get("created_at", now),
                wake_event_type=p.get("wake_event_type"),
                wake_match=p.get("wake_match"),
                origin_episode_id=p.get("origin_episode_id"),
                origin_stimulus_id=p.get("origin_stimulus_id"),
                trigger_event_id=p.get("trigger_event_id"),
                origin_mode=p.get("origin_mode", "live")
            )
            if task.id not in self._known_task_ids:
                self._known_task_ids.add(task.id)
                heapq.heappush(self._heap, task)

    async def _scheduler_loop(self) -> None:
        last_sweep = self.event_store.clock()
        while self._running:
            try:
                now = self.event_store.clock()

                # Periodic anti-drift sync
                if now - last_sweep >= self.sweep_interval:
                    await self._sync_from_db()
                    for event in await self.event_store.pending_runtime_events():
                        await self.emit_event(event)
                    last_sweep = now

                if not self._heap:
                    # Wait for new task notification or sweep interval
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=self.sweep_interval)
                        self._wake_event.clear()
                    except asyncio.TimeoutError:
                        pass
                    continue

                earliest = self._heap[0]
                if earliest.due_at <= now:
                    task = heapq.heappop(self._heap)
                    self._known_task_ids.discard(task.id)
                    await self._emit_task_due(task, now)
                else:
                    sleep_time = max(0.05, min(earliest.due_at - now, self.sweep_interval))
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_time)
                        self._wake_event.clear()
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in scheduler loop: %s", e)
                await asyncio.sleep(1.0)

    async def run_due(self, now: float) -> int:
        """Drive the same durable claims explicitly when replay owns the clock."""
        await self._sync_from_db()
        count = 0
        while self._heap and self._heap[0].due_at <= now:
            task = heapq.heappop(self._heap)
            self._known_task_ids.discard(task.id)
            count += bool(await self._emit_task_due(task, now))
        return count

    async def _emit_task_due(
        self,
        task: TaskItem,
        now: float,
        trigger_event_id: str = "",
    ) -> bool:
        """Emit immutable TASK_DUE Event (ADR-0009 & ADR-0018 & ADR-0029).
        Durable task claim is enforced before emitting TASK_DUE."""
        if task.payload.get("kind") == "agent_job" and not self.jobs_enabled_probe():
            return False
        event = Event(
            event_type=EventType.TASK_DUE,
            scene_id=task.scene_id,
            actor_id="system:scheduler",
            timestamp=now,
            payload={
                "task_id": task.id,
                "trigger_event_id": trigger_event_id,
                "raw_text": task.description,
                "description": task.description,
                "payload": task.payload,
                "origin_mode": getattr(task, "origin_mode", "live"),
            }
        )
        if not await self.event_store.claim_task_event(task.id, task.scene_id, event):
            return False
        logger.info("Task due! Triggering task %s (%s) for scene %s (origin=%s)",
                    task.id, task.description, task.scene_id, getattr(task, "origin_mode", "live"))
        await self.emit_event(event)
        return True

    async def on_event(self, event: Event) -> list[str]:
        """ADR-0018 & ADR-0029: fire condition-bound obligations matching a committed event.
        A condition task fires when its wake_event_type and exact wake_match dictionary match
        in its scene, or at its due_at deadline — whichever comes first.
        """
        wake_type = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        matched = []
        for t in self._heap:
            if t.wake_event_type != wake_type or t.scene_id != event.scene_id or t.id not in self._known_task_ids:
                continue
            # Structured wake_match evaluation (ADR-0029, §16)
            if t.wake_match:
                ev_payload = event.payload if isinstance(event.payload, dict) else {}
                match = True
                for k, expected_v in t.wake_match.items():
                    if ev_payload.get(k) != expected_v:
                        match = False
                        break
                if not match:
                    continue
            matched.append(t)

        fired_ids: list[str] = []
        for task in matched:
            self._known_task_ids.discard(task.id)
            self._heap = [t for t in self._heap if t.id != task.id]
            heapq.heapify(self._heap)
            if await self._emit_task_due(task, self.event_store.clock(), trigger_event_id=event.id):
                fired_ids.append(task.id)

        if fired_ids and self.metrics:
            self.metrics.inc_social("tasks_started", len(fired_ids))
        return fired_ids

    async def cancel_task(self, task_id: str) -> bool:
        """Cancels a scheduled task in heap and database."""
        self._known_task_ids.discard(task_id)
        self._heap = [t for t in self._heap if t.id != task_id]
        heapq.heapify(self._heap)
        await self.event_store.mark_task_status(task_id, TaskStatus.CANCELLED.value)
        logger.info("Task %s cancelled manually", task_id)
        return True

    async def trigger_task_now(self, task_id: str) -> bool:
        """Immediately triggers a pending task with durable claim."""
        target_task = None
        for t in self._heap:
            if t.id == task_id:
                target_task = t
                break

        if not target_task:
            tasks = await self.event_store.get_pending_tasks()
            for p in tasks:
                if p["id"] == task_id:
                    target_task = TaskItem(
                        id=p["id"],
                        scene_id=p["scene_id"],
                        description=p["description"],
                        due_at=self.event_store.clock(),
                        status=TaskStatus.PENDING,
                        payload=p.get("payload", {}) if isinstance(p.get("payload"), dict) else {},
                        wake_match=p.get("wake_match"),
                        origin_episode_id=p.get("origin_episode_id"),
                        origin_stimulus_id=p.get("origin_stimulus_id"),
                        origin_mode=p.get("origin_mode", "live")
                    )
                    break

        if not target_task:
            return False

        self._known_task_ids.discard(task_id)
        self._heap = [t for t in self._heap if t.id != task_id]
        heapq.heapify(self._heap)

        return await self._emit_task_due(target_task, self.event_store.clock(), trigger_event_id="manual:trigger_now")
