"""Scoped reads for the single social-knowledge ledger.

All belief writes use the proposal transaction in memory.writes. Reading a
memory never increments a score or otherwise changes its apparent reliability.
"""

import asyncio
import heapq
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import aiosqlite

from len_bot.memory.models import MemoryItem
from len_bot.tools.discovery import rank_discovery


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
        offset: int = 0,
        start_time: float | None = None,
        end_time: float | None = None,
        subject_aliases: Mapping[tuple[str, str], Sequence[str]] | None = None,
    ) -> list[MemoryItem]:
        """Filter ledger facts first, then rank text within those identities.

        Times cover creation of a ledger record, with an exclusive end. Aliases
        must come from the caller's same-scene participant facts; they help find
        records but never replace a subject ID or establish a new relationship.
        """
        if type(limit) is not int or limit < 1:
            raise ValueError('Memory search requires a positive integer limit')
        if type(offset) is not int or offset < 0:
            raise ValueError('Memory search requires a non-negative integer offset')
        for value in (start_time, end_time):
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value)):
                raise ValueError('Memory time bounds must be finite timestamps')
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise ValueError('Memory end_time must be later than start_time')
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
        if start_time is not None:
            sql += " AND created_at>=?"
            params.append(start_time)
        if end_time is not None:
            sql += " AND created_at<?"
            params.append(end_time)
        sql += " ORDER BY created_at DESC,id"
        if not query or not query.strip():
            rows = await (await self._db.execute(sql + " LIMIT ? OFFSET ?", [*params, limit, offset])).fetchall()
            return [memory_from_row(row) for row in rows]

        address_sql = f"""SELECT scope,subject,statement FROM memories
            WHERE scope IN ({','.join('?' for _ in scopes)}) AND kind='address'
              AND basis='reported' AND status='active' AND (expires_at IS NULL OR expires_at>?)"""
        address_params: list[Any] = [*scopes, self.clock()]
        if subject is not None:
            address_sql += " AND subject=?"
            address_params.append(subject)
        address_terms: dict[tuple[str, str], list[str]] = {}
        async with self._db.execute(address_sql, address_params) as cursor:
            async for scope, identity, statement in cursor:
                address_terms.setdefault((scope, identity), []).append(statement)

        # Only retain the requested page of best matches while visiting the
        # already scoped/type/time/status-filtered ledger. No hidden recent-row
        # cutoff can prevent an older but more relevant fact from being found;
        # the page offset walks that same complete ranking deterministically.
        ranked: list[tuple[tuple[int, ...], float, str, MemoryItem]] = []
        async with self._db.execute(sql, params) as cursor:
            async for row in cursor:
                memory = memory_from_row(row)
                identity = (memory.scope, memory.subject)
                aliases = tuple(subject_aliases.get(identity, ())) if subject_aliases else ()
                score = rank_discovery(query, name=memory.subject, aliases=aliases,
                    keywords=tuple(address_terms.get(identity, ())), description=memory.statement)
                if score is None:
                    continue
                entry = (score, memory.created_at, memory.id, memory)
                if len(ranked) < limit + offset:
                    heapq.heappush(ranked, entry)
                else:
                    heapq.heappushpop(ranked, entry)
        ordered = [entry[3] for entry in sorted(ranked, reverse=True)]
        return ordered[offset:offset + limit]

    async def get_memory_in_scopes(self, memory_id: str, allowed_scopes: list[str]) -> MemoryItem | None:
        scopes = list(dict.fromkeys(allowed_scopes))
        if not scopes:
            return None
        row = await (await self._db.execute(
            f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE id=? AND scope IN ({','.join('?' for _ in scopes)})",
            [memory_id, *scopes],
        )).fetchone()
        return memory_from_row(row) if row is not None else None

    async def get_memories_by_ids(self, memory_ids: list[str], allowed_scopes: list[str], *, include_superseded=False,
                                  subject: str | None = None, kind: str | None = None,
                                  start_time: float | None = None, end_time: float | None = None) -> list[MemoryItem]:
        if not memory_ids or not allowed_scopes: return []
        clauses = [f"id IN ({','.join('?' for _ in memory_ids)})",
                   f"scope IN ({','.join('?' for _ in allowed_scopes)})"]
        params: list[Any] = [*memory_ids, *allowed_scopes]
        if not include_superseded:
            clauses.append("status='active' AND (expires_at IS NULL OR expires_at>?)")
            params.append(self.clock())
        if subject is not None:
            clauses.append("subject=?"); params.append(subject)
        if kind is not None:
            clauses.append("kind=?"); params.append(kind)
        if start_time is not None:
            clauses.append("created_at>=?"); params.append(start_time)
        if end_time is not None:
            clauses.append("created_at<?"); params.append(end_time)
        rows = await (await self._db.execute(
            f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE {' AND '.join(clauses)}", params)).fetchall()
        by_id={item.id:item for item in (memory_from_row(row) for row in rows)}
        return [by_id[item] for item in memory_ids if item in by_id]

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
