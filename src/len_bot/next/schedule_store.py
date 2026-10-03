"""Scene arrangements and their queries on the shared conversation database."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store


SCHEMA = """
CREATE TABLE schedules (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL,
    created REAL NOT NULL, due_at REAL NOT NULL,
    timezone TEXT NOT NULL, note TEXT NOT NULL,
    target TEXT NOT NULL, requester TEXT,
    status TEXT NOT NULL, delivered_at REAL, reason TEXT,
    interval_seconds INTEGER CHECK(interval_seconds BETWEEN 60 AND 31536000),
    cron TEXT CHECK(cron IS NULL OR interval_seconds IS NULL),
    legacy_source TEXT
);
CREATE INDEX schedules_status_due ON schedules(scene,status,due_at,id);
CREATE UNIQUE INDEX schedules_legacy_identity ON schedules(scene,json_extract(legacy_source,'$.task.id'))
    WHERE legacy_source IS NOT NULL;
"""


@dataclass(frozen=True)
class Schedule:
    id: int
    scene: str
    created: float
    due_at: float
    timezone: str
    note: str
    target: str
    requester: str | None
    status: str
    delivered_at: float | None
    reason: str | None
    interval_seconds: int | None = None
    cron: str | None = None
    legacy_source: dict | None = None


class ScheduleStore:
    def __init__(self, store: Store):
        self.db, self.now = store.db, store.now

    @staticmethod
    def _schedule(row: sqlite3.Row) -> Schedule:
        values = dict(row)
        if values['legacy_source'] is not None:
            raw = values['legacy_source']
            try:
                values['legacy_source'] = json.loads(raw)
            except ValueError as error:
                raise ValueError(f'Invalid original reminder source: {error}; raw={raw[:500]!r}') from error
        return Schedule(**values)

    def create_schedule(self, scene: str, *, due_at: float, timezone: str,
                        note: str, target: str, requester: str | None,
                        limit: int, interval_seconds: int | None = None,
                        cron: str | None = None) -> Schedule:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            unfinished = self.db.execute(
                "SELECT COUNT(*) FROM schedules WHERE scene=? AND status IN ('pending','blocked')",
                (scene,),
            ).fetchone()[0]
            if unfinished >= limit:
                raise ValueError(f"Scene {scene} has reached its unfinished schedule limit {limit}")
            cursor = self.db.execute(
                "INSERT INTO schedules(scene,created,due_at,timezone,note,target,requester,status,"
                "interval_seconds,cron) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                (scene, self.now(), due_at, timezone, note, target, requester,
                 interval_seconds, cron),
            )
            row = self.db.execute(
                "SELECT * FROM schedules WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._schedule(row)

    def get_schedule(self, scene: str, id: int) -> Schedule:
        row = self.db.execute(
            "SELECT * FROM schedules WHERE scene=? AND id=?", (scene, id)
        ).fetchone()
        if row is None:
            raise ValueError(f"Scene {scene} has no schedule {id}")
        return self._schedule(row)

    def list_schedules(self, scene: str, *, status: str = "active", offset: int = 0,
                       limit: int = 20) -> list[Schedule]:
        if status == "all":
            where, parameters = "", ()
        elif status == "active":
            where, parameters = " AND status IN ('pending','blocked')", ()
        else:
            where, parameters = " AND status=?", (status,)
        rows = self.db.execute(
            "SELECT * FROM schedules WHERE scene=?" + where + " ORDER BY due_at,id LIMIT ? OFFSET ?",
            (scene, *parameters, limit, offset),
        ).fetchall()
        return [self._schedule(row) for row in rows]

    def next_schedule_at(self, scene: str) -> float | None:
        return self.db.execute(
            "SELECT MIN(due_at) FROM schedules WHERE scene=? AND status='pending'", (scene,)
        ).fetchone()[0]

    def due_schedules(self, scene: str, now: float) -> list[Schedule]:
        rows = self.db.execute(
            "SELECT * FROM schedules WHERE scene=? AND status='pending' AND due_at<=? "
            "ORDER BY due_at,id", (scene, now),
        ).fetchall()
        return [self._schedule(row) for row in rows]

    def cancel_schedule(self, scene: str, id: int) -> Schedule:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.get_schedule(scene, id)
            if current.status not in {"pending", "blocked"}:
                raise ValueError(f"Schedule {id} is {current.status}, not pending or blocked")
            self.db.execute(
                "UPDATE schedules SET status='cancelled' WHERE scene=? AND id=?", (scene, id)
            )
            result = self.get_schedule(scene, id)
        return result

    def block_schedule(self, scene: str, id: int, reason: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.get_schedule(scene, id)
            if current.status != "pending":
                raise ValueError(f"Schedule {id} is {current.status}, not pending")
            self.db.execute(
                "UPDATE schedules SET status='blocked',reason=? WHERE scene=? AND id=?",
                (reason, scene, id),
            )
