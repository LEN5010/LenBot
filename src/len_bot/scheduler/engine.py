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

    async def _emit_task_due(
        self,
        task: TaskItem,
        now: float,
        trigger_event_id: str = "",
        skip_claim: bool = False
    ) -> None:
        """Emit immutable TASK_DUE Event (ADR-0009 & ADR-0018 & ADR-0029).
        Durable task claim is enforced before emitting TASK_DUE."""
        if not skip_claim:
            claimed = await self.event_store.claim_task(task.id, trigger_event_id=trigger_event_id or f"timer:{now}")
            if not claimed:
                logger.info("Task %s was already claimed by another worker; skipping TASK_DUE emit", task.id)
                return

        event = Event(
            event_type=EventType.TASK_DUE,
            scene_id=task.scene_id,
            actor_id="system:scheduler",
            timestamp=now,
            payload={
                "task_id": task.id,
                "raw_text": task.description,
                "description": task.description,
                "payload": task.payload,
                "origin_mode": getattr(task, "origin_mode", "live"),
            }
        )
        logger.info("Task due! Triggering task %s (%s) for scene %s (origin=%s)",
                    task.id, task.description, task.scene_id, getattr(task, "origin_mode", "live"))
        await self.emit_event(event)

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
            claimed = await self.event_store.claim_task(task.id, trigger_event_id=event.id)
            if claimed:
                fired_ids.append(task.id)
                await self._emit_task_due(task, time.time(), trigger_event_id=event.id, skip_claim=True)

        if fired_ids and self.metrics:
            self.metrics.inc_social("obligations_fulfilled", len(fired_ids))
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
                        due_at=time.time(),
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

        claimed = await self.event_store.claim_task(target_task.id, trigger_event_id="manual:trigger_now")
        if not claimed:
            return False

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
                "payload": target_task.payload,
                "origin_mode": getattr(target_task, "origin_mode", "live"),
            }
        )
        logger.info("Task %s triggered manually now", task_id)
        await self.emit_event(event)
        return True

    async def promote_task(self, task_id: str) -> Optional[dict]:
        """ADR-0029, §23.4: Promotes/duplicates task as a renewed template task with live origin."""
        if not self.event_store._db:
            return None
        cursor = await self.event_store._db.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        import uuid
        import json
        new_id = f"promoted_{uuid.uuid4().hex[:8]}_{task_id}"
        payload_data = json.loads(row[6]) if row[6] and isinstance(row[6], str) else (row[6] or {})
        wake_match_data = json.loads(row[9]) if len(row) > 9 and row[9] and isinstance(row[9], str) else None

        new_task = TaskItem(
            id=new_id,
            scene_id=row[1],
            description=f"[Promoted] {row[2]}",
            due_at=time.time() + 86400.0,
            status=TaskStatus.PENDING,
            source_event_id=f"promoted:{task_id}",
            payload=payload_data,
            wake_event_type=row[8] if len(row) > 8 else None,
            wake_match=wake_match_data,
            origin_episode_id=row[10] if len(row) > 10 else None,
            origin_stimulus_id=row[11] if len(row) > 11 else None,
            origin_mode="live",
        )
        await self.event_store.save_task(new_task)
        self.schedule_task(new_task)
        return new_task.model_dump()
