"""Actual extraction ranges, processing results and model exchanges.

This database belongs to memory processing, not the chat history or a backend's
rebuildable index. Enabling memory for the first time starts with new messages.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import stat
import time
from typing import Iterator

from ..storage.store import encode
from .embeddings import _reject_constant


FORMAT_VERSION = 5


class MemoryJobs:
    def __init__(self, path: Path, *, readonly: bool = False):
        self.db = (sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                   if readonly else sqlite3.connect(path))
        self.db.row_factory = sqlite3.Row
        try:
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                application_id = self.db.execute("PRAGMA application_id").fetchone()[0]
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if application_id == 0x4C424D4A and version in range(1, FORMAT_VERSION):
                    raise ValueError(
                        f"Memory processing database format {version} requires offline migration while stopped: "
                        f"{path}; run python -m len_bot.next.maintenance.migrate_memory_jobs from the instance directory")
                if application_id != 0x4C424D4A or version != FORMAT_VERSION:
                    raise ValueError(f"unsupported memory processing database: {path}")
            else:
                if readonly:
                    raise ValueError(f'Existing memory processing database is not initialized: {path}')
                self.db.executescript("""
                    BEGIN;
                    PRAGMA application_id=1279413578;
                    PRAGMA user_version=5;
                    CREATE TABLE memory_cursors (
                        scene TEXT PRIMARY KEY, after_seq INTEGER NOT NULL,
                        enabled_at REAL NOT NULL
                    );
                    CREATE TABLE memory_personas (
                        scene TEXT NOT NULL, persona_id TEXT NOT NULL,
                        PRIMARY KEY(scene,persona_id)
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
                    CREATE TABLE memory_summary_runs (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, scope TEXT NOT NULL, path TEXT NOT NULL,
                        started REAL NOT NULL, ended REAL,
                        status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
                        request TEXT NOT NULL, response TEXT, usage TEXT, cost TEXT, error TEXT, model_started REAL
                    );
                    CREATE INDEX memory_summary_runs_path ON memory_summary_runs(scope,path,id);
                    CREATE TABLE memory_embedding_calls (
                        id INTEGER PRIMARY KEY, scene TEXT NOT NULL, purpose TEXT NOT NULL,
                        started REAL NOT NULL, ended REAL, request TEXT NOT NULL,
                        response TEXT, usage TEXT, cost TEXT, error TEXT
                    );
                    CREATE INDEX memory_embedding_usage ON memory_embedding_calls(started,scene);
                    COMMIT;
                """)
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def recover_embeddings(self, now: float) -> None:
        error = 'Interrupted: previous memory embedding call has no confirmed result; not replayed; service usage and cost remain unknown'
        with self.db:
            self.db.execute("UPDATE memory_embedding_calls SET ended=?,error=CASE WHEN error IS NULL THEN ? "
                            "ELSE error || char(10) || ? END WHERE ended IS NULL", (now, error, error))

    @staticmethod
    def _embedding_record(row: sqlite3.Row, *, request: bool) -> dict:
        result = dict(row)
        fields = ('request', 'response', 'usage', 'cost') if request else ('response', 'usage', 'cost')
        for field in fields:
            raw = result[field]
            if raw is None:
                continue
            try:
                result[field] = json.loads(raw, parse_constant=_reject_constant)
            except ValueError as error:
                raise ValueError(f'Invalid memory embedding call {row["id"]} {field}: {error}; raw={raw[:500]!r}') from error
        return result

    def embedding_calls(self, source: str, *, offset: int, limit: int, snapshot: int | None) -> dict:
        maximum = self.db.execute('SELECT COALESCE(MAX(id),0) FROM memory_embedding_calls WHERE scene=?',
                                  (source,)).fetchone()[0]
        boundary = maximum if snapshot is None else snapshot
        if boundary > maximum:
            raise ValueError(f'Embedding call snapshot is beyond this source: source={source!r}, snapshot={boundary}, maximum={maximum}')
        rows = self.db.execute('SELECT id,scene,purpose,started,ended,response,usage,cost,error '
            'FROM memory_embedding_calls WHERE scene=? AND id<=? ORDER BY id DESC LIMIT ? OFFSET ?',
            (source, boundary, limit + 1, offset)).fetchall()
        return {'source': source, 'snapshot': boundary, 'offset': offset,
                'next_offset': offset + limit if len(rows) > limit else None,
                'calls': [self._embedding_record(row, request=False) for row in rows[:limit]]}

    def embedding_call(self, source: str, id: int) -> dict:
        row = self.db.execute('SELECT * FROM memory_embedding_calls WHERE scene=? AND id=?', (source, id)).fetchone()
        if row is None:
            raise FileNotFoundError(f'Memory embedding call {id} does not exist in source {source!r}')
        return self._embedding_record(row, request=True)

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

    def remember_personas(self, scene: str, persona_ids: set[str]) -> None:
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO memory_personas(scene,persona_id) VALUES(?,?)',
                                ((scene, persona_id) for persona_id in sorted(persona_ids)))

    def persona_ids(self, scene: str) -> set[str]:
        return {row[0] for row in self.db.execute('SELECT persona_id FROM memory_personas WHERE scene=?', (scene,))}

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

    def recover_summaries(self) -> None:
        with self.db:
            self.db.execute("UPDATE memory_summary_runs SET status='interrupted',ended=?,error=? "
                            "WHERE status='running'",
                            (time.time(), "Process stopped before this summary completed"))

    def begin_summary(self, scene: str, scope: str, path: str, request: dict) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO memory_summary_runs(scene,scope,path,started,status,request,model_started) VALUES(?,?,?,?,'running',?,?)",
                (scene, scope, path, time.time(), encode(request), time.time()),
            ).lastrowid

    def summary_response(self, id: int, response: object, usage: object, cost: object) -> None:
        with self.db:
            self.db.execute("UPDATE memory_summary_runs SET response=?,usage=?,cost=? WHERE id=?",
                            (encode(response), None if usage is None else encode(usage),
                             None if cost is None else encode(cost), id))

    def finish_summary(self, id: int, status: str, error: str | None = None) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE memory_summary_runs SET status=?,ended=?,error=? WHERE id=? AND status='running'",
                (status, time.time(), error, id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Memory summary run {id} is not running")

    def summary_runs(self, scope: str, path: str, *, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            "SELECT id,scene,scope,path,started,ended,status,usage,cost,error FROM memory_summary_runs "
            "WHERE scope=? AND path=? ORDER BY id DESC LIMIT ?", (scope, path, limit),
        ).fetchall()
        return [{**dict(row), "usage": None if row["usage"] is None else json.loads(row["usage"]),
                 "cost": None if row["cost"] is None else json.loads(row["cost"])} for row in rows]

    def summary_run(self, id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM memory_summary_runs WHERE id=?", (id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), **{key: None if row[key] is None else json.loads(row[key])
                                for key in ("request", "response", "usage", "cost")}}


@contextmanager
def processing_records(database: Path, active: MemoryJobs | None) -> Iterator[MemoryJobs | None]:
    """Borrow the active owner or read this root's existing records without starting memory."""
    if active is not None:
        yield active
        return
    path = database.with_name(database.name + '.memory.sqlite3')
    try:
        info = path.lstat()
    except FileNotFoundError:
        yield None
        return
    if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path:
        raise ValueError(f'Memory record source must be an existing regular file without links: {path}; mode={oct(info.st_mode)}')
    with MemoryJobs(path, readonly=True) as records:
        yield records
