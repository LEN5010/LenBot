"""Actual extraction ranges, resumable service receipts and model exchanges.

This database belongs to memory processing, not the chat history or a backend's
rebuildable index. Enabling memory for the first time starts with new messages.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from .store import encode


class MemoryJobs:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if application_id == 0x4C424D4A and version == 1:
                    raise ValueError(
                        f"Memory processing database format 1 requires offline migration while stopped: {path}; "
                        "run python -m len_bot.next.migrate_memory_jobs from the instance directory")
                if application_id != 0x4C424D4A or version != 2:
                    raise ValueError(f"unsupported memory processing database: {path}")
            else:
                self.db.executescript("""
                    BEGIN;
                    PRAGMA application_id=1279413578;
                    PRAGMA user_version=2;
                    CREATE TABLE memory_cursors (
                        scene TEXT PRIMARY KEY, after_seq INTEGER NOT NULL,
                        enabled_at REAL NOT NULL
                    );
                    CREATE TABLE memory_jobs (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, backend TEXT NOT NULL,
                        first_seq INTEGER NOT NULL, through_seq INTEGER NOT NULL,
                        status TEXT NOT NULL, started REAL NOT NULL, ended REAL,
                        details TEXT NOT NULL, error TEXT
                    );
                    CREATE INDEX memory_jobs_scene ON memory_jobs(scene,id);
                    CREATE TABLE memory_exclusions (
                        scene TEXT NOT NULL, message_seq INTEGER NOT NULL,
                        PRIMARY KEY(scene,message_seq)
                    );
                    COMMIT;
                """)
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> MemoryJobs:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def initialize(self, scene: str, latest_seq: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO memory_cursors VALUES(?,?,?)",
                            (scene, latest_seq, time.time()))
            self.db.execute("UPDATE memory_jobs SET status='interrupted',ended=?,error=? "
                            "WHERE scene=? AND status='running'",
                            (time.time(), "Process stopped before this extraction completed", scene))

    def after(self, scene: str) -> int:
        return self.db.execute("SELECT after_seq FROM memory_cursors WHERE scene=?", (scene,)).fetchone()[0]

    def exclude_records(self, scene: str, records: list[int]) -> int:
        """Persist selected real message positions; ownership is checked by the host."""
        with self.db:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO memory_exclusions(scene,message_seq) VALUES(?,?)",
                ((scene, record) for record in records),
            )
            return self.db.total_changes - before

    def excluded_records(self, scene: str, after: int = 0,
                         through: int | None = None) -> list[int]:
        conditions = ["scene=?", "message_seq>?"]
        values: list[object] = [scene, after]
        if through is not None:
            conditions.append("message_seq<=?")
            values.append(through)
        rows = self.db.execute(
            "SELECT message_seq FROM memory_exclusions WHERE " + " AND ".join(conditions)
            + " ORDER BY message_seq", values,
        ).fetchall()
        return [row[0] for row in rows]

    def latest(self, scene: str) -> dict | None:
        row = self.db.execute("SELECT * FROM memory_jobs WHERE scene=? ORDER BY id DESC LIMIT 1",
                              (scene,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["details"] = json.loads(result["details"])
        return result

    def create(self, scene: str, backend: str, first: int, through: int) -> dict:
        with self.db:
            self.db.execute("INSERT INTO memory_jobs(scene,backend,first_seq,through_seq,status,started,details) "
                            "VALUES(?,?,?,?,'queued',?,?)",
                            (scene, backend, first, through, time.time(), encode({"calls": [], "writes": [], "tools": []})))
        return self.latest(scene)

    def details(self, job: dict) -> None:
        with self.db:
            self.db.execute("UPDATE memory_jobs SET details=? WHERE id=?",
                            (encode(job["details"]), job["id"]))

    def status(self, job: dict, status: str, error: str | None = None) -> None:
        ended = None if status in {"queued", "running", "submitted"} else time.time()
        with self.db:
            self.db.execute("UPDATE memory_jobs SET status=?,error=?,ended=?,details=? WHERE id=?",
                            (status, error, ended, encode(job["details"]), job["id"]))
            if status == "complete":
                self.db.execute("UPDATE memory_cursors SET after_seq=? WHERE scene=?",
                                (job["through_seq"], job["scene"]))
        job.update(status=status, error=error, ended=ended)

    def view(self, scene: str) -> dict:
        job = self.latest(scene)
        cursor = self.db.execute("SELECT after_seq,enabled_at FROM memory_cursors WHERE scene=?", (scene,)).fetchone()
        return {"scene": scene, "after_seq": cursor["after_seq"], "enabled_at": cursor["enabled_at"],
                "latest": job}
