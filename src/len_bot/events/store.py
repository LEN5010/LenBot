import aiosqlite
import json
import time
from typing import Any, Optional
from len_bot.events.models import Event, EventType

class EventStore:
    def __init__(self, db_path: str = "len_bot.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        
        # 1. Raw Event Store
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                payload TEXT NOT NULL,
                metadata TEXT NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_events_scene ON events(scene_id, timestamp);")

        # 2. SQLite Trigram FTS5 for CJK Message Search (ADR-0005)
        try:
            await self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    event_id UNINDEXED,
                    scene_id UNINDEXED,
                    actor_id UNINDEXED,
                    content,
                    tokenize='trigram'
                );
            """)
        except Exception:
            # Fallback for environments where trigram might not be compiled
            await self._db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
                    event_id UNINDEXED,
                    scene_id UNINDEXED,
                    actor_id UNINDEXED,
                    content
                );
            """)

        # 3. Materialized State Tables (ADR-0001)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS scene_states (
                scene_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS open_loops (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                target_actor_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_open_loops_active ON open_loops(scene_id, status);")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                description TEXT NOT NULL,
                due_at REAL NOT NULL,
                status TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, due_at);")

        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def append_event(self, event: Event) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        
        payload_str = json.dumps(event.payload, ensure_ascii=False)
        metadata_str = json.dumps(event.metadata, ensure_ascii=False)

        await self._db.execute(
            """
            INSERT INTO events (id, event_type, scene_id, actor_id, timestamp, payload, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp, payload_str, metadata_str)
        )

        # Index text content into FTS5
        text = event.raw_text
        if text:
            await self._db.execute(
                """
                INSERT INTO events_fts (event_id, scene_id, actor_id, content)
                VALUES (?, ?, ?, ?);
                """,
                (event.id, event.scene_id, event.actor_id, text)
            )

        await self._db.commit()

    async def get_recent_events(self, scene_id: str, limit: int = 50) -> list[Event]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        
        cursor = await self._db.execute(
            """
            SELECT id, event_type, scene_id, actor_id, timestamp, payload, metadata
            FROM events
            WHERE scene_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?;
            """,
            (scene_id, limit)
        )
        rows = await cursor.fetchall()
        events = []
        for r in reversed(rows):
            events.append(Event(
                id=r[0],
                event_type=EventType(r[1]),
                scene_id=r[2],
                actor_id=r[3],
                timestamp=r[4],
                payload=json.loads(r[5]),
                metadata=json.loads(r[6])
            ))
        return events

    async def search_messages(self, query: str, allowed_scopes: list[str], limit: int = 20) -> list[dict[str, Any]]:
        """ADR-0006: Execution Scope enforced strictly at SQL query layer."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if not allowed_scopes:
            return []

        placeholders = ",".join("?" for _ in allowed_scopes)
        # SQLite trigram requires query length >= 3 for MATCH
        if len(query) >= 3:
            match_clause = "f.content MATCH ?"
            query_param = query
        else:
            match_clause = "f.content LIKE ?"
            query_param = f"%{query}%"

        sql = f"""
            SELECT e.id, e.event_type, e.scene_id, e.actor_id, e.timestamp, e.payload
            FROM events_fts f
            JOIN events e ON f.event_id = e.id
            WHERE {match_clause}
              AND e.scene_id IN ({placeholders})
            ORDER BY e.timestamp DESC
            LIMIT ?;
        """
        params = [query_param, *allowed_scopes, limit]
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "event_type": r[1],
                "scene_id": r[2],
                "actor_id": r[3],
                "timestamp": r[4],
                "payload": json.loads(r[5]),
            }
            for r in rows
        ]

    async def load_scene_state(self, scene_id: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            "SELECT version, state_json FROM scene_states WHERE scene_id = ?;",
            (scene_id,)
        )
        row = await cursor.fetchone()
        if row:
            data = json.loads(row[1])
            data["version"] = row[0]
            return data
        return None

    async def save_scene_state(self, scene_id: str, version: int, state_data: dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        await self._db.execute(
            """
            INSERT INTO scene_states (scene_id, version, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scene_id) DO UPDATE SET
                version = excluded.version,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at;
            """,
            (scene_id, version, json.dumps(state_data, ensure_ascii=False), time.time())
        )
        await self._db.commit()

    async def get_active_open_loops(self, scene_id: str) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT id, scene_id, target_actor_id, intent, source_event_id, status, created_at, expires_at
            FROM open_loops
            WHERE scene_id = ? AND status = 'active'
            ORDER BY created_at ASC;
            """,
            (scene_id,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "scene_id": r[1],
                "target_actor_id": r[2],
                "intent": r[3],
                "source_event_id": r[4],
                "status": r[5],
                "created_at": r[6],
                "expires_at": r[7],
            }
            for r in rows
        ]

    async def save_open_loop(self, loop_data: dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        await self._db.execute(
            """
            INSERT INTO open_loops (id, scene_id, target_actor_id, intent, source_event_id, status, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET status = excluded.status;
            """,
            (
                loop_data["id"],
                loop_data["scene_id"],
                loop_data["target_actor_id"],
                loop_data["intent"],
                loop_data["source_event_id"],
                loop_data["status"],
                loop_data["created_at"],
                loop_data["expires_at"]
            )
        )
        await self._db.commit()
