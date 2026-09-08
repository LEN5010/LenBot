"""Scoped reads for the single social-knowledge ledger.

All belief writes use the proposal transaction in memory.writes. Reading a
memory never increments a score or otherwise changes its apparent reliability.
"""

import asyncio
import json
import time
from collections.abc import Callable, Sequence
from typing import Any

import aiosqlite

from len_bot.memory.models import MemoryItem


MEMORY_COLUMNS = (
    "id", "scope", "subject", "kind", "statement", "basis", "evidence", "status",
    "expires_at", "created_at", "revision", "created_event_id", "revision_event_id",
    "supersedes_ids", "superseded_by", "revision_reason", "revision_evidence",
)
_JSON_COLUMNS = {"evidence", "supersedes_ids", "revision_evidence"}


def memory_from_row(row: Sequence[Any]) -> MemoryItem:
    values = dict(zip(MEMORY_COLUMNS, row, strict=True))
    for key in _JSON_COLUMNS:
        values[key] = json.loads(values[key])
    return MemoryItem.model_validate(values)


class MemoryStore:
    def __init__(
        self,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ):
        self._db = db
        self.write_lock = write_lock or asyncio.Lock()
        self.clock = clock or time.time

    async def initialize(self) -> None:
        async with self.write_lock:
            columns = await (await self._db.execute("PRAGMA table_info(memories)")).fetchall()
            if columns and {row[1] for row in columns} != set(MEMORY_COLUMNS):
                raise RuntimeError("非现行认识结构；保持停机，使用对应旧版本完成离线处理后再启动")
            await self._db.execute("""CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                subject TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('address','preference','relationship','fact','group_norm')),
                statement TEXT NOT NULL,
                basis TEXT NOT NULL CHECK(basis IN ('reported','inferred')),
                evidence TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active','superseded','refuted')),
                expires_at REAL,
                created_at REAL NOT NULL,
                revision INTEGER NOT NULL,
                created_event_id TEXT,
                revision_event_id TEXT,
                supersedes_ids TEXT NOT NULL,
                superseded_by TEXT,
                revision_reason TEXT NOT NULL,
                revision_evidence TEXT NOT NULL
            )""")
            await self._db.execute("CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(scope,subject,kind,status)")
            await self._db.commit()

    async def query_memories(
        self,
        allowed_scopes: list[str],
        subject: str | None = None,
        kind: str | None = None,
        query: str | None = None,
        include_superseded: bool = False,
        *, limit: int,
    ) -> list[MemoryItem]:
        if type(limit) is not int or limit < 1:
            raise ValueError('Memory search requires a positive integer limit')
        scopes = list(dict.fromkeys(allowed_scopes))
        if not scopes:
            return []
        sql = f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE scope IN ({','.join('?' for _ in scopes)})"
        params: list[Any] = list(scopes)
        if not include_superseded:
            sql += " AND status='active' AND (expires_at IS NULL OR expires_at>?)"
            params.append(self.clock())
        if subject is not None:
            sql += " AND subject=?"
            params.append(subject)
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        if query:
            sql += " AND statement LIKE ? ESCAPE '\\'"
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        sql += " ORDER BY created_at DESC,id LIMIT ?"
        params.append(limit)
        rows = await (await self._db.execute(sql, params)).fetchall()
        return [memory_from_row(row) for row in rows]

    async def get_memory_in_scopes(self, memory_id: str, allowed_scopes: list[str]) -> MemoryItem | None:
        scopes = list(dict.fromkeys(allowed_scopes))
        if not scopes:
            return None
        row = await (await self._db.execute(
            f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE id=? AND scope IN ({','.join('?' for _ in scopes)})",
            [memory_id, *scopes],
        )).fetchone()
        return memory_from_row(row) if row is not None else None

    async def interaction_preferences(
        self, scene_id: str, participant_ids: list[str], now: float | None = None,
    ) -> list[MemoryItem]:
        """Directly stated interaction preferences for the current participants.

        This is a local projection, not automatic semantic retrieval.
        Inferred traits and personal facts are read only through memory tools.
        """
        subjects = list(dict.fromkeys([scene_id, *participant_ids]))
        rows = await (await self._db.execute(
            f"""SELECT {','.join(MEMORY_COLUMNS)} FROM memories
                WHERE scope=? AND subject IN ({','.join('?' for _ in subjects)})
                  AND kind IN ('address','preference','group_norm')
                  AND basis='reported' AND status='active'
                  AND (expires_at IS NULL OR expires_at>?)
                ORDER BY created_at,id""",
            [scene_id, *subjects, self.clock() if now is None else now],
        )).fetchall()
        return [memory_from_row(row) for row in rows]
