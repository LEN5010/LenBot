import aiosqlite
import json
import logging
import time
from typing import Optional, Any
from len_bot.memory.models import EpisodeRecord, MemoryItem, MemoryStatus, MemoryCertainty

logger = logging.getLogger(__name__)

class MemoryStore:
    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        # L1 Episodes
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_event_ids TEXT NOT NULL,
                participants TEXT NOT NULL,
                tags TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_episodes_scene ON episodes(scene_id, created_at);")

        # L2 Semantic / Social Memories
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                temporal TEXT NOT NULL,
                certainty TEXT NOT NULL,
                scope TEXT NOT NULL,
                visibility TEXT NOT NULL,
                evidence TEXT NOT NULL,
                status TEXT NOT NULL,
                human_readable_assertion TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_confirmed_at REAL NOT NULL
            );
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_slot ON memories(subject, kind, key, scope, status);"
        )
        await self._db.commit()

    async def save_episode(self, record: EpisodeRecord) -> None:
        await self._db.execute("""
            INSERT INTO episodes (id, scene_id, title, summary, source_event_ids, participants, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            record.id,
            record.scene_id,
            record.title,
            record.summary,
            json.dumps(record.source_event_ids, ensure_ascii=False),
            json.dumps(record.participants, ensure_ascii=False),
            json.dumps(record.tags, ensure_ascii=False),
            record.created_at
        ))
        await self._db.commit()

    async def get_episode(self, episode_id: str) -> Optional[EpisodeRecord]:
        cursor = await self._db.execute(
            "SELECT id, scene_id, title, summary, source_event_ids, participants, tags, created_at FROM episodes WHERE id = ?;",
            (episode_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return EpisodeRecord(
            id=row[0],
            scene_id=row[1],
            title=row[2],
            summary=row[3],
            source_event_ids=json.loads(row[4]),
            participants=json.loads(row[5]),
            tags=json.loads(row[6]),
            created_at=row[7]
        )

    async def get_episodes(self, scene_id: str, limit: int = 10) -> list[EpisodeRecord]:
        cursor = await self._db.execute(
            "SELECT id, scene_id, title, summary, source_event_ids, participants, tags, created_at FROM episodes WHERE scene_id = ? ORDER BY created_at DESC LIMIT ?;",
            (scene_id, limit)
        )
        rows = await cursor.fetchall()
        return [
            EpisodeRecord(
                id=r[0], scene_id=r[1], title=r[2], summary=r[3],
                source_event_ids=json.loads(r[4]), participants=json.loads(r[5]),
                tags=json.loads(r[6]), created_at=r[7]
            )
            for r in rows
        ]

    async def find_active_memory(self, subject: str, kind: str, key: str, scope: str) -> Optional[MemoryItem]:
        cursor = await self._db.execute("""
            SELECT id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status, human_readable_assertion, created_at, last_confirmed_at
            FROM memories
            WHERE subject = ? AND kind = ? AND key = ? AND scope = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1;
        """, (subject, kind, key, scope))
        row = await cursor.fetchone()
        if not row:
            return None
        return MemoryItem(
            id=row[0], subject=row[1], kind=row[2], key=row[3], value=row[4],
            temporal=row[5], certainty=MemoryCertainty(row[6]), scope=row[7],
            visibility=row[8], evidence=json.loads(row[9]), status=MemoryStatus(row[10]),
            human_readable_assertion=row[11], created_at=row[12], last_confirmed_at=row[13]
        )

    async def save_memory(self, item: MemoryItem) -> None:
        await self._db.execute("""
            INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status, human_readable_assertion, created_at, last_confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                value = excluded.value,
                certainty = excluded.certainty,
                last_confirmed_at = excluded.last_confirmed_at;
        """, (
            item.id, item.subject, item.kind, item.key, item.value, item.temporal,
            item.certainty.value, item.scope, item.visibility,
            json.dumps(item.evidence, ensure_ascii=False), item.status.value,
            item.human_readable_assertion, item.created_at, item.last_confirmed_at
        ))
        await self._db.commit()

    async def update_memory_status(self, memory_id: str, status: MemoryStatus) -> None:
        await self._db.execute(
            "UPDATE memories SET status = ? WHERE id = ?;",
            (status.value, memory_id)
        )
        await self._db.commit()

    async def query_memories(
        self,
        allowed_scopes: list[str],
        subject: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 15
    ) -> list[MemoryItem]:
        if not allowed_scopes:
            return []
        placeholders = ",".join("?" for _ in allowed_scopes)
        sql = f"SELECT id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status, human_readable_assertion, created_at, last_confirmed_at FROM memories WHERE status = 'active' AND scope IN ({placeholders})"
        params: list[Any] = list(allowed_scopes)

        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)

        sql += " ORDER BY last_confirmed_at DESC LIMIT ?;"
        params.append(limit)

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            MemoryItem(
                id=r[0], subject=r[1], kind=r[2], key=r[3], value=r[4],
                temporal=r[5], certainty=MemoryCertainty(r[6]), scope=r[7],
                visibility=r[8], evidence=json.loads(r[9]), status=MemoryStatus(r[10]),
                human_readable_assertion=r[11], created_at=r[12], last_confirmed_at=r[13]
            )
            for r in rows
        ]
