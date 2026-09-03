import asyncio
import aiosqlite
import json
import logging
import time
from typing import Optional, Any
from len_bot.memory.models import EpisodeRecord, MemoryItem, MemoryStatus, MemoryCertainty

logger = logging.getLogger(__name__)

class MemoryStore:
    def __init__(self, db: aiosqlite.Connection, write_lock: Optional[asyncio.Lock] = None):
        self._db = db
        self.write_lock = write_lock or asyncio.Lock()

    async def initialize(self) -> None:
        async with self.write_lock:
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
                    superseded_by TEXT,
                    access_count INTEGER DEFAULT 0,
                    last_accessed_at REAL,
                    decay_score REAL DEFAULT 1.0,
                    human_readable_assertion TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_confirmed_at REAL NOT NULL
                );
            """)
            await self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_slot ON memories(subject, kind, key, scope, status);"
            )

            # Migrations for existing DB schemas
            for col, col_type in [
                ("superseded_by", "TEXT"),
                ("access_count", "INTEGER DEFAULT 0"),
                ("last_accessed_at", "REAL"),
                ("decay_score", "REAL DEFAULT 1.0"),
            ]:
                try:
                    await self._db.execute(f"ALTER TABLE memories ADD COLUMN {col} {col_type};")
                except Exception:
                    pass

            # ADR-0019: canonical MemoryKind values. Legacy free-form 'pattern' folds
            # into 'social_pattern' (one-time data migration, not a dual semantic).
            await self._db.execute("UPDATE memories SET kind = 'social_pattern' WHERE kind = 'pattern';")

            # Reflection cursor (ADR-0019 §10.4): per-scene last reflected event rowid.
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS reflection_cursors (
                    scene_id TEXT PRIMARY KEY,
                    last_event_rowid INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

            await self._db.commit()

    async def save_episode(self, record: EpisodeRecord) -> None:
        async with self.write_lock:
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
            SELECT id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status,
                   superseded_by, access_count, last_accessed_at, decay_score, human_readable_assertion, created_at, last_confirmed_at
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
            superseded_by=row[11], access_count=row[12] or 0, last_accessed_at=row[13],
            decay_score=row[14] if row[14] is not None else 1.0,
            human_readable_assertion=row[15], created_at=row[16], last_confirmed_at=row[17]
        )

    async def save_memory(self, item: MemoryItem) -> None:
        async with self.write_lock:
            await self._db.execute("""
                INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status,
                                      superseded_by, access_count, last_accessed_at, decay_score, human_readable_assertion, created_at, last_confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    value = excluded.value,
                    certainty = excluded.certainty,
                    superseded_by = excluded.superseded_by,
                    access_count = excluded.access_count,
                    last_accessed_at = excluded.last_accessed_at,
                    decay_score = excluded.decay_score,
                    last_confirmed_at = excluded.last_confirmed_at;
            """, (
                item.id, item.subject, item.kind, item.key, item.value, item.temporal,
                item.certainty.value, item.scope, item.visibility,
                json.dumps(item.evidence, ensure_ascii=False), item.status.value,
                item.superseded_by, item.access_count, item.last_accessed_at, item.decay_score,
                item.human_readable_assertion, item.created_at, item.last_confirmed_at
            ))
            await self._db.commit()

    async def update_memory_status(self, memory_id: str, status: MemoryStatus, superseded_by: Optional[str] = None) -> None:
        async with self.write_lock:
            if superseded_by:
                await self._db.execute(
                    "UPDATE memories SET status = ?, superseded_by = ? WHERE id = ?;",
                    (status.value, superseded_by, memory_id)
                )
            else:
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
        key: Optional[str] = None,
        query: Optional[str] = None,
        include_superseded: bool = False,
        limit: int = 15
    ) -> list[MemoryItem]:
        if not allowed_scopes:
            return []
        now = time.time()
        placeholders = ",".join("?" for _ in allowed_scopes)

        # Invariant G: Strict SQL boundary with visibility='global' support (Goal 4 & ADR-0006)
        status_clause = "status IN ('active', 'superseded')" if include_superseded else "status = 'active'"
        sql = f"""
            SELECT id, subject, kind, key, value, temporal, certainty, scope, visibility, evidence, status,
                   superseded_by, access_count, last_accessed_at, decay_score, human_readable_assertion, created_at, last_confirmed_at
            FROM memories
            WHERE {status_clause} AND (scope IN ({placeholders}) OR visibility = 'global')
        """
        params: list[Any] = list(allowed_scopes)

        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if key:
            sql += " AND key = ?"
            params.append(key)
        if query:
            sql += " AND (human_readable_assertion LIKE ? OR value LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])

        sql += " ORDER BY last_confirmed_at DESC LIMIT ?;"
        params.append(limit)

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        memories = [
            MemoryItem(
                id=r[0], subject=r[1], kind=r[2], key=r[3], value=r[4],
                temporal=r[5], certainty=MemoryCertainty(r[6]), scope=r[7],
                visibility=r[8], evidence=json.loads(r[9]), status=MemoryStatus(r[10]),
                superseded_by=r[11], access_count=r[12] or 0, last_accessed_at=r[13],
                decay_score=r[14] if r[14] is not None else 1.0,
                human_readable_assertion=r[15], created_at=r[16], last_confirmed_at=r[17]
            )
            for r in rows
        ]

        # Update access count & recency for queried active memories
        if memories and not include_superseded:
            active_ids = [m.id for m in memories if m.status == MemoryStatus.ACTIVE]
            if active_ids:
                id_placeholders = ",".join("?" for _ in active_ids)
                async with self.write_lock:
                    await self._db.execute(
                        f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({id_placeholders});",
                        [now, *active_ids]
                    )
                    await self._db.commit()

        return memories

    async def get_memory_history(self, allowed_scopes: list[str], subject: str, kind: str, key: str) -> list[MemoryItem]:
        """Retrieves full evolution trajectory of a semantic slot (active and superseded)."""
        return await self.query_memories(
            allowed_scopes=allowed_scopes,
            subject=subject,
            kind=kind,
            key=key,
            include_superseded=True,
            limit=10
        )

    async def get_reflection_cursor(self, scene_id: str) -> int:
        cursor = await self._db.execute(
            "SELECT last_event_rowid FROM reflection_cursors WHERE scene_id = ?;",
            (scene_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def set_reflection_cursor(self, scene_id: str, last_event_rowid: int) -> None:
        async with self.write_lock:
            await self._db.execute("""
                INSERT INTO reflection_cursors (scene_id, last_event_rowid, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    last_event_rowid = excluded.last_event_rowid,
                    updated_at = excluded.updated_at;
            """, (scene_id, last_event_rowid, time.time()))
            await self._db.commit()

    async def decay_memories(self, current_time: Optional[float] = None, half_life_days: float = 30.0) -> int:
        """Decays memory scores based on elapsed time and access count. Archives dead tentative memories."""
        now = current_time or time.time()
        half_life_seconds = half_life_days * 86400.0
        async with self.write_lock:
            cursor = await self._db.execute(
                "SELECT id, certainty, last_confirmed_at, last_accessed_at, access_count, decay_score FROM memories WHERE status = 'active';"
            )
            rows = await cursor.fetchall()
            updated_count = 0
            for r in rows:
                mid, certainty_str, confirmed_at, accessed_at, access_count, score = r
                last_active = accessed_at or confirmed_at
                elapsed = max(0.0, now - last_active)
                
                # Exponential decay formula: factor = 0.5 ** (elapsed / half_life)
                factor = 0.5 ** (elapsed / half_life_seconds)
                new_score = round(max(0.05, min(1.0, factor * (1.0 + min(1.0, (access_count or 0) * 0.1)))), 3)
                
                # Tentative memories with score <= 0.25 (e.g. >= 2 half-lives unconfirmed) forgotten
                if new_score <= 0.25 and certainty_str == "tentative":
                    await self._db.execute("UPDATE memories SET status = 'forgotten', decay_score = ? WHERE id = ?;", (new_score, mid))
                else:
                    await self._db.execute("UPDATE memories SET decay_score = ? WHERE id = ?;", (new_score, mid))
                updated_count += 1
            await self._db.commit()
            return updated_count
