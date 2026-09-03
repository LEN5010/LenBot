import asyncio
import heapq
import logging
import time
from typing import Optional, Callable, Awaitable
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.scheduler.models import TaskItem, TaskStatus

logger = logging.getLogger(__name__)

class TaskScheduler:
    def __init__(
        self,
        event_store: EventStore,
        emit_event: Callable[[Event], Awaitable[None]],
        sweep_interval: float = 10.0
    ):
        self.event_store = event_store
        self.emit_event = emit_event
        self.sweep_interval = sweep_interval
        self._heap: list[TaskItem] = []
        self._wake_event = asyncio.Event()
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._known_task_ids: set[str] = set()

    async def start(self) -> None:
        self._running = True
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
        now = time.time()
        pending = await self.event_store.get_pending_tasks()
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
                wake_event_type=p.get("wake_event_type")
            )
            if task.id not in self._known_task_ids:
                self._known_task_ids.add(task.id)
                heapq.heappush(self._heap, task)

    async def _scheduler_loop(self) -> None:
        last_sweep = time.time()
        while self._running:
            try:
                now = time.time()

                # Periodic anti-drift sync
                if now - last_sweep >= self.sweep_interval:
                    await self._sync_from_db()
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

    async def _emit_task_due(self, task: TaskItem, now: float) -> None:
        """Emit immutable TASK_DUE Event (ADR-0009 & ADR-0018).
        SceneActor will atomically commit the event AND mark task triggered in SQLite in one transaction!"""
        event = Event(
            event_type=EventType.TASK_DUE,
            scene_id=task.scene_id,
            actor_id="system:scheduler",
            timestamp=now,
            payload={
                "task_id": task.id,
                "raw_text": task.description,
                "description": task.description,
                "payload": task.payload
            }
        )
        logger.info("Task due! Triggering task %s (%s) for scene %s", task.id, task.description, task.scene_id)
        await self.emit_event(event)

    async def on_event(self, event: Event) -> list[str]:
        """ADR-0018: fire condition-bound obligations matching a committed event.

        Called by AgentRuntime AFTER the SceneActor atomically committed the event.
        A condition task fires when its wake_event_type arrives in its scene, or at
        its due_at deadline — whichever comes first. Firing always takes the same
        TASK_DUE authority path as timer tasks.
        """
        wake_type = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        matched = [
            t for t in self._heap
            if t.wake_event_type == wake_type and t.scene_id == event.scene_id and t.id in self._known_task_ids
        ]
        fired_ids: list[str] = []
        for task in matched:
            self._known_task_ids.discard(task.id)
            self._heap = [t for t in self._heap if t.id != task.id]
            heapq.heapify(self._heap)
            fired_ids.append(task.id)
            await self._emit_task_due(task, time.time())
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
        """Immediately triggers a pending task."""
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
                        due_at=time.time(),
                        status=TaskStatus.PENDING,
                        payload=p.get("payload", {}) if isinstance(p.get("payload"), dict) else {}
                    )
                    break

        if not target_task:
            return False

        self._known_task_ids.discard(task_id)
        self._heap = [t for t in self._heap if t.id != task_id]
        heapq.heapify(self._heap)

        now = time.time()
        event = Event(
            event_type=EventType.TASK_DUE,
            scene_id=target_task.scene_id,
            actor_id="system:scheduler",
            timestamp=now,
            payload={
                "task_id": target_task.id,
                "raw_text": target_task.description,
                "description": target_task.description,
                "payload": target_task.payload
            }
        )
        logger.info("Task %s triggered manually now", task_id)
        await self.emit_event(event)
        return True
