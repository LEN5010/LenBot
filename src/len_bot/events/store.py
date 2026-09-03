import asyncio
import aiosqlite
import json
import time
from typing import Any, Optional
from len_bot.events.models import Event, EventType

class EventStore:
    def __init__(self, db_path: str = "len_bot.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

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

        # 4. Dashboard Users & Dynamic Configurations
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_login_at REAL
            );
        """)

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS runtime_dynamic_configs (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        """)

        await self._db.commit()

    async def get_dashboard_user(self, username: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            "SELECT username, password_hash, created_at, last_login_at FROM dashboard_users WHERE username = ?;",
            (username,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "username": row[0],
            "password_hash": row[1],
            "created_at": row[2],
            "last_login_at": row[3],
        }

    async def create_dashboard_user(self, username: str, password_hash: str) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        now = time.time()
        async with self._write_lock:
            await self._db.execute(
                "INSERT OR IGNORE INTO dashboard_users (username, password_hash, created_at) VALUES (?, ?, ?);",
                (username, password_hash, now)
            )
            await self._db.commit()

    async def update_dashboard_user_password(self, username: str, password_hash: str) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._db.execute(
                "UPDATE dashboard_users SET password_hash = ? WHERE username = ?;",
                (password_hash, username)
            )
            await self._db.commit()

    async def update_dashboard_user_login(self, username: str) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._db.execute(
                "UPDATE dashboard_users SET last_login_at = ? WHERE username = ?;",
                (time.time(), username)
            )
            await self._db.commit()

    async def get_dynamic_config(self, key: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            "SELECT value_json FROM runtime_dynamic_configs WHERE key = ?;",
            (key,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])

    async def save_dynamic_config(self, key: str, value: dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        now = time.time()
        val_str = json.dumps(value, ensure_ascii=False)
        async with self._write_lock:
            await self._db.execute("""
                INSERT INTO runtime_dynamic_configs (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at;
            """, (key, val_str, now))
            await self._db.commit()

    async def get_stats(self) -> dict[str, int]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        c1 = await self._db.execute("SELECT COUNT(*) FROM events;")
        (event_count,) = await c1.fetchone()
        c2 = await self._db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending';")
        (task_count,) = await c2.fetchone()
        c3 = await self._db.execute("SELECT COUNT(*) FROM open_loops WHERE status = 'active';")
        (loop_count,) = await c3.fetchone()
        c4 = await self._db.execute("SELECT COUNT(*) FROM scene_states;")
        (scene_count,) = await c4.fetchone()
        return {
            "total_events": event_count,
            "pending_tasks": task_count,
            "active_open_loops": loop_count,
            "total_scenes": scene_count,
        }

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def append_event(self, event: Event) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        
        payload_str = json.dumps(event.payload, ensure_ascii=False)
        metadata_str = json.dumps(event.metadata, ensure_ascii=False)

        async with self._write_lock:
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

    async def read_context(
        self,
        event_id: str,
        before: int = 3,
        after: int = 3,
        allowed_scopes: Optional[list[str]] = None
    ) -> list[dict[str, Any]]:
        """Reads surrounding events around a specific event, respecting allowed scopes."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if not allowed_scopes:
            return []

        placeholders = ",".join("?" for _ in allowed_scopes)
        # 1. Fetch target event
        target_sql = f"""
            SELECT id, event_type, scene_id, actor_id, timestamp, payload
            FROM events
            WHERE id = ? AND scene_id IN ({placeholders});
        """
        cursor = await self._db.execute(target_sql, [event_id, *allowed_scopes])
        target_row = await cursor.fetchone()
        if not target_row:
            return []

        t_id, t_etype, t_scene, t_actor, t_time, t_payload = target_row
        target_dict = {
            "id": t_id, "event_type": t_etype, "scene_id": t_scene,
            "actor_id": t_actor, "timestamp": t_time, "payload": json.loads(t_payload)
        }

        # 2. Fetch before events
        before_sql = f"""
            SELECT id, event_type, scene_id, actor_id, timestamp, payload
            FROM events
            WHERE scene_id = ? AND timestamp < ?
            ORDER BY timestamp DESC
            LIMIT ?;
        """
        b_cursor = await self._db.execute(before_sql, [t_scene, t_time, before])
        b_rows = await b_cursor.fetchall()
        before_list = [
            {"id": r[0], "event_type": r[1], "scene_id": r[2], "actor_id": r[3], "timestamp": r[4], "payload": json.loads(r[5])}
            for r in reversed(b_rows)
        ]

        # 3. Fetch after events
        after_sql = f"""
            SELECT id, event_type, scene_id, actor_id, timestamp, payload
            FROM events
            WHERE scene_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT ?;
        """
        a_cursor = await self._db.execute(after_sql, [t_scene, t_time, after])
        a_rows = await a_cursor.fetchall()
        after_list = [
            {"id": r[0], "event_type": r[1], "scene_id": r[2], "actor_id": r[3], "timestamp": r[4], "payload": json.loads(r[5])}
            for r in a_rows
        ]

        return before_list + [target_dict] + after_list

    async def query_timeline(
        self,
        scene_id: str,
        start_time: float,
        end_time: float,
        allowed_scopes: Optional[list[str]] = None,
        limit: int = 20
    ) -> list[dict[str, Any]]:
        """Queries events in a time window for a scene, guarded by execution scope."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if not allowed_scopes or scene_id not in allowed_scopes:
            return []

        sql = """
            SELECT id, event_type, scene_id, actor_id, timestamp, payload
            FROM events
            WHERE scene_id = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?;
        """
        cursor = await self._db.execute(sql, [scene_id, start_time, end_time, limit])
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "event_type": r[1], "scene_id": r[2], "actor_id": r[3], "timestamp": r[4], "payload": json.loads(r[5])}
            for r in rows
        ]

    async def query_person_history(
        self,
        actor_id: str,
        allowed_scopes: Optional[list[str]] = None,
        limit: int = 15
    ) -> list[dict[str, Any]]:
        """Queries events by a specific actor across permitted scenes."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if not allowed_scopes:
            return []

        placeholders = ",".join("?" for _ in allowed_scopes)
        sql = f"""
            SELECT id, event_type, scene_id, actor_id, timestamp, payload
            FROM events
            WHERE actor_id = ? AND scene_id IN ({placeholders})
            ORDER BY timestamp DESC
            LIMIT ?;
        """
        cursor = await self._db.execute(sql, [actor_id, *allowed_scopes, limit])
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "event_type": r[1], "scene_id": r[2], "actor_id": r[3], "timestamp": r[4], "payload": json.loads(r[5])}
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
        async with self._write_lock:
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

    async def commit_scene_event(
        self,
        event: Event,
        scene_state_data: dict[str, Any],
        task_id_to_trigger: Optional[str] = None,
        associated_open_loop: Optional[dict[str, Any]] = None
    ) -> None:
        """P0.1, P0.4 & Item 3: Atomically commit Event, FTS, SceneState, optional Task triggered status, and OpenLoop activation."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        async with self._write_lock:
            payload_str = json.dumps(event.payload, ensure_ascii=False)
            metadata_str = json.dumps(event.metadata, ensure_ascii=False)

            # 1. Insert Event
            await self._db.execute(
                """
                INSERT INTO events (id, event_type, scene_id, actor_id, timestamp, payload, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp, payload_str, metadata_str)
            )

            # 2. Insert FTS5
            text = event.raw_text
            if text:
                await self._db.execute(
                    """
                    INSERT INTO events_fts (event_id, scene_id, actor_id, content)
                    VALUES (?, ?, ?, ?);
                    """,
                    (event.id, event.scene_id, event.actor_id, text)
                )

            # 3. If event triggers a task, update task status atomically in the same transaction
            if task_id_to_trigger:
                await self._db.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?;",
                    ("triggered", task_id_to_trigger)
                )

            # 4. Item 3: If message sent event has associated open loop, activate it atomically in the same transaction!
            if associated_open_loop:
                await self._db.execute(
                    """
                    INSERT INTO open_loops (id, scene_id, target_actor_id, intent, source_event_id, status, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET status = excluded.status;
                    """,
                    (
                        associated_open_loop["id"],
                        associated_open_loop["scene_id"],
                        associated_open_loop["target_actor_id"],
                        associated_open_loop["intent"],
                        event.id,
                        associated_open_loop["status"],
                        associated_open_loop["created_at"],
                        associated_open_loop["expires_at"]
                    )
                )

            # 5. Upsert SceneState
            version = scene_state_data.get("version", 0)
            await self._db.execute(
                """
                INSERT INTO scene_states (scene_id, version, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    version = excluded.version,
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at;
                """,
                (event.scene_id, version, json.dumps(scene_state_data, ensure_ascii=False), time.time())
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
        async with self._write_lock:
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

    async def create_task(self, task_data: dict[str, Any]) -> None:
        """P0.1: Dedicated write authority for tasks under write_lock."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO tasks (id, scene_id, description, due_at, status, source_event_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_data["id"],
                    task_data["scene_id"],
                    task_data["description"],
                    task_data["due_at"],
                    task_data["status"],
                    task_data["source_event_id"],
                    json.dumps(task_data.get("payload", {}), ensure_ascii=False) if not isinstance(task_data.get("payload"), str) else task_data.get("payload"),
                    task_data["created_at"]
                )
            )
            await self._db.commit()

    async def get_pending_tasks(self, max_due_at: Optional[float] = None) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        sql = "SELECT id, scene_id, description, due_at, status, source_event_id, payload, created_at FROM tasks WHERE status = 'pending'"
        params: list[Any] = []
        if max_due_at is not None:
            sql += " AND due_at <= ?"
            params.append(max_due_at)
        sql += " ORDER BY due_at ASC;"
        
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()

        def _parse_payload(raw: Any) -> Any:
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str) and raw.strip():
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
            return {}

        return [
            {
                "id": r[0],
                "scene_id": r[1],
                "description": r[2],
                "due_at": r[3],
                "status": r[4],
                "source_event_id": r[5],
                "payload": _parse_payload(r[6]),
                "created_at": r[7]
            }
            for r in rows
        ]

    async def mark_task_status(self, task_id: str, status: str) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._db.execute(
                "UPDATE tasks SET status = ? WHERE id = ?;",
                (status, task_id)
            )
            await self._db.commit()

    async def expire_open_loops(self, now: float) -> list[str]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            cursor = await self._db.execute(
                "SELECT id FROM open_loops WHERE status = 'active' AND expires_at <= ?;",
                (now,)
            )
            rows = await cursor.fetchall()
            expired_ids = [r[0] for r in rows]
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                await self._db.execute(
                    f"UPDATE open_loops SET status = 'expired' WHERE id IN ({placeholders});",
                    expired_ids
                )
                await self._db.commit()
            return expired_ids
