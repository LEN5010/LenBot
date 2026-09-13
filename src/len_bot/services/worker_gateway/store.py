"""The Gateway's own journal: the same facts, in the Gateway's own database.

The Gateway must be able to say what it accepted, what it is running and
whether a container is really gone *while LenBot is down*.  That is why this
is its own SQLite file and not a table in LenBot's database: the record that
outlives a control-service outage cannot live only in the control service.

The schema, the state machine and the event ordering are the host's own
(`len_bot.execution.journal`), reused rather than re-implemented — one
definition of the contract used by two processes, instead of two copies that
can disagree about what a state change means.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import aiosqlite

from len_bot.execution.journal import ExecutionJournalMixin


class GatewayStore(ExecutionJournalMixin):
    def __init__(self, database_path: str, clock=time.time):
        self.clock = clock
        self.database_path = database_path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.database_path)
        await self._db.execute('PRAGMA journal_mode=WAL;')
        await self._db.execute('PRAGMA synchronous=NORMAL;')
        await self.initialize_executions()
        # A registered artifact belongs to one execution and to no other.  The
        # row is what a download is looked up by, so a caller can only fetch a
        # file this Gateway already listed — never a path it composed itself.
        await self._db.execute("""CREATE TABLE IF NOT EXISTS execution_artifacts (
            artifact_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, media_type TEXT NOT NULL, registered_at REAL NOT NULL,
            UNIQUE(execution_id,path))""")
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def unfinished(self) -> list[str]:
        """Executions this Gateway still owes an outcome for, oldest first."""
        rows = await (await self._db.execute(
            "SELECT execution_id FROM execution_runs WHERE state IN"
            " ('accepted','starting','running','cancel_requested') ORDER BY accepted_at")).fetchall()
        return [row[0] for row in rows]

    async def count_unfinished(self) -> int:
        row = await (await self._db.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE state IN"
            " ('accepted','starting','running','cancel_requested')")).fetchone()
        return int(row[0] or 0)

    async def events_for(self, execution_id: str) -> list[tuple[int, str]]:
        """Sequence and kind only: enough to tell a restart what already happened."""
        rows = await (await self._db.execute(
            "SELECT sequence,kind FROM execution_events WHERE execution_id=? ORDER BY sequence",
            (execution_id,))).fetchall()
        return [(int(row[0]), row[1]) for row in rows]

    async def register_artifact(self, execution_id: str, path: str, size_bytes: int,
                                media_type: str) -> dict:
        """Record one listed output file under a stable id of its own."""
        existing = await (await self._db.execute(
            "SELECT artifact_id,size_bytes,media_type FROM execution_artifacts"
            " WHERE execution_id=? AND path=?", (execution_id, path))).fetchone()
        if existing is not None:
            return {'artifact_id': existing[0], 'execution_id': execution_id, 'path': path,
                    'size_bytes': int(existing[1]), 'media_type': existing[2]}
        artifact_id = uuid.uuid4().hex
        await self._db.execute(
            "INSERT INTO execution_artifacts(artifact_id,execution_id,path,size_bytes,media_type,"
            "registered_at) VALUES(?,?,?,?,?,?)",
            (artifact_id, execution_id, path, size_bytes, media_type, self.clock()))
        await self._db.commit()
        return {'artifact_id': artifact_id, 'execution_id': execution_id, 'path': path,
                'size_bytes': size_bytes, 'media_type': media_type}

    async def artifact(self, artifact_id: str) -> dict | None:
        row = await (await self._db.execute(
            "SELECT artifact_id,execution_id,path,size_bytes,media_type FROM execution_artifacts"
            " WHERE artifact_id=?", (artifact_id,))).fetchone()
        if row is None:
            return None
        return {'artifact_id': row[0], 'execution_id': row[1], 'path': row[2],
                'size_bytes': int(row[3]), 'media_type': row[4]}

    async def artifacts_for(self, execution_id: str) -> list[dict]:
        rows = await (await self._db.execute(
            "SELECT artifact_id,execution_id,path,size_bytes,media_type FROM execution_artifacts"
            " WHERE execution_id=? ORDER BY path", (execution_id,))).fetchall()
        return [{'artifact_id': row[0], 'execution_id': row[1], 'path': row[2],
                 'size_bytes': int(row[3]), 'media_type': row[4]} for row in rows]
