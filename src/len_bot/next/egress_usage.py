"""Known task and scene-day egress bytes over existing connection events."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from len_bot.services.worker_gateway.egress_policy import EgressBlocked

from .config import HostConfig
from .tasks_store import TaskStore


_FLUSH_BYTES = 1024 * 1024
_FLUSH_SECONDS = 1.0


@dataclass
class _Connection:
    event_id: int
    body: dict
    saved_up: int
    saved_down: int
    last_flush: float
    day_for_write: str


class EgressUsage:
    def __init__(self, config: HostConfig, records: TaskStore,
                 on_update: Callable[[str], None]):
        self.config = config
        self.records = records
        self.on_update = on_update
        self._connections: dict[tuple[str, int, int], _Connection] = {}
        self._tasks: dict[tuple[str, int], list[int]] = {}
        self._scene_days: dict[tuple[str, str], list[int]] = {}
        self._last_notice: dict[str, float] = {}

    def recover(self) -> int:
        """At host startup, close old unfinished rows without inventing lost bytes."""
        count = self.records.recover_egress()
        self._connections.clear()
        self._tasks.clear()
        self._scene_days.clear()
        self._last_notice.clear()
        return count

    def _date(self) -> str:
        return datetime.fromtimestamp(self.records.now(), ZoneInfo(self.config.timezone)).date().isoformat()

    def _task(self, scene: str, task_id: int) -> list[int]:
        key = (scene, task_id)
        if key not in self._tasks:
            self._tasks[key] = list(self.records.egress_task_totals(scene, task_id))
        return self._tasks[key]

    def _day(self, scene: str, date: str) -> list[int]:
        key = (scene, date)
        if key not in self._scene_days:
            self._scene_days[key] = list(self.records.egress_scene_day_totals(scene, date))
        return self._scene_days[key]

    def _limits(self, scene: str) -> tuple[int, int]:
        common = self.config.worker.egress
        local = self.config.scenes[scene].tasks
        return (
            common.max_task_bytes if local.egress_max_task_bytes is None else local.egress_max_task_bytes,
            common.max_scene_daily_bytes if local.egress_max_daily_bytes is None
            else local.egress_max_daily_bytes,
        )

    def on_connection(self, scene: str, task_id: int, facts: dict) -> None:
        wire_id = facts["connection_id"]
        key = (scene, task_id, wire_id)
        phase = facts["phase"]
        if phase == "start":
            if not self.config.worker.egress.enabled:
                raise EgressBlocked("当前宿主未开启公共出网")
            task_limit, daily_limit = self._limits(scene)
            task = self._task(scene, task_id)
            date = self._date()
            day = self._day(scene, date)
            if sum(task) >= task_limit:
                raise EgressBlocked(f"任务 #{task_id} 已达累计出网限额 {task_limit} 字节")
            if sum(day) >= daily_limit:
                raise EgressBlocked(f"场景 {scene} 在 {date} 已达出网限额 {daily_limit} 字节")
            now = self.records.now()
            body = {
                "host": facts["host"], "port": facts["port"],
                "ip": None, "method": facts["method"],
                "started": facts["started"], "ended": None,
                "status": "opening", "error": None,
                "up": 0, "down": 0, "days": {}, "saved_at": now,
            }
            event_id = self.records.start_egress_connection(scene, task_id, body)
            self._connections[key] = _Connection(event_id, body, 0, 0, time.monotonic(), date)
            self._last_notice[scene] = time.monotonic()
            self.on_update(scene)
            return
        connection = self._connections[key]
        if phase == "connected":
            connection.body["ip"] = facts["ip"]
            connection.body["status"] = "connected"
            connection.body["saved_at"] = self.records.now()
            self.records.update_egress_connection(scene, task_id, connection.event_id,
                                                  connection.body)
            connection.last_flush = time.monotonic()
            return
        if phase != "end":
            raise ValueError(f"Unknown egress connection phase: {phase!r}")
        connection.body["ip"] = facts["ip"]
        connection.body["ended"] = facts["ended"]
        connection.body["error"] = facts["error"]
        connection.body["status"] = "ended"
        connection.body["saved_at"] = self.records.now()
        self.records.update_egress_connection(scene, task_id, connection.event_id,
                                              connection.body)
        self._connections.pop(key)
        self._last_notice[scene] = time.monotonic()
        self.on_update(scene)

    def before_bytes(self, scene: str, task_id: int, wire_id: int,
                     side: str, amount: int) -> None:
        connection = self._connections[(scene, task_id, wire_id)]
        task_limit, daily_limit = self._limits(scene)
        task = self._task(scene, task_id)
        date = self._date()
        day = self._day(scene, date)
        if sum(task) + amount > task_limit:
            raise EgressBlocked(f"任务 #{task_id} 出网字节会超过限额 {task_limit}；本块未转发")
        if sum(day) + amount > daily_limit:
            raise EgressBlocked(f"场景 {scene} 在 {date} 出网字节会超过限额 {daily_limit}；本块未转发")
        connection.day_for_write = date

    def on_bytes(self, scene: str, task_id: int, wire_id: int,
                 side: str, amount: int) -> None:
        connection = self._connections[(scene, task_id, wire_id)]
        date = connection.day_for_write
        task = self._task(scene, task_id)
        day = self._day(scene, date)
        position = 0 if side == "up" else 1
        connection.body[side] += amount
        counts = connection.body["days"].setdefault(date, {"up": 0, "down": 0})
        counts[side] += amount
        task[position] += amount
        day[position] += amount
        now = time.monotonic()
        unsaved = (connection.body["up"] - connection.saved_up
                   + connection.body["down"] - connection.saved_down)
        if unsaved >= _FLUSH_BYTES or now - connection.last_flush >= _FLUSH_SECONDS:
            connection.body["saved_at"] = self.records.now()
            self.records.update_egress_connection(scene, task_id, connection.event_id,
                                                  connection.body)
            connection.saved_up = connection.body["up"]
            connection.saved_down = connection.body["down"]
            connection.last_flush = now
        if now - self._last_notice[scene] >= _FLUSH_SECONDS:
            self._last_notice[scene] = now
            self.on_update(scene)

    def release_task(self, scene: str, task_id: int) -> None:
        if any(key[:2] == (scene, task_id) for key in self._connections):
            raise ValueError(f"Task {task_id} in {scene} still has an active egress connection")
        self._tasks.pop((scene, task_id), None)

    def status(self, scene: str, task_id: int | None = None) -> dict:
        task_limit, daily_limit = self._limits(scene)
        if task_id is None:
            date = self._date()
            up, down = self._day(scene, date)
            limit = daily_limit
        else:
            date = None
            up, down = self._task(scene, task_id)
            limit = task_limit
        return {
            "up": up, "down": down, "limit": limit, "date": date,
            "incomplete_connections": self.records.egress_incomplete_count(scene, task_id),
            "last_error": self.records.egress_last_error(scene, task_id),
            "enabled": self.config.worker.egress.enabled,
        }
