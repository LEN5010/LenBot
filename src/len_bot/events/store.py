import asyncio
import aiosqlite
import json
import logging
import time
import uuid
from typing import Any, Optional
from len_bot.events.models import Event, EventType
from len_bot.memory.writes import validate_memory_proposal, commit_memory_proposal_core
from len_bot.memory.models import EpisodeRecord, MemoryProposal, MemoryItem

logger = logging.getLogger(__name__)

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
            CREATE TABLE IF NOT EXISTS group_agent_sessions (
                scene_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                last_observed_event_rowid INTEGER NOT NULL,
                last_cognized_event_rowid INTEGER NOT NULL,
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
                created_at REAL NOT NULL,
                wake_event_type TEXT,
                wake_match_json TEXT,
                origin_episode_id TEXT,
                origin_stimulus_id TEXT,
                trigger_event_id TEXT,
                origin_mode TEXT DEFAULT 'live'
            );
        """)
        # Migrations for databases created in earlier stages (ADR-0018 & ADR-0029)
        for col, col_type in [
            ("wake_event_type", "TEXT"),
            ("wake_match_json", "TEXT"),
            ("origin_episode_id", "TEXT"),
            ("origin_stimulus_id", "TEXT"),
            ("trigger_event_id", "TEXT"),
            ("origin_mode", "TEXT DEFAULT 'live'"),
        ]:
            try:
                await self._db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_type};")
            except Exception:
                pass
        try:
            await self._db.execute("ALTER TABLE open_loops ADD COLUMN source_stimulus_id TEXT;")
        except Exception:
            pass
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

        # ADR-0022: behavior trace store — attention evaluations & full episode chains
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS traces (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                scene_id TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_traces_scene ON traces(scene_id, created_at);")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_traces_kind ON traces(kind, created_at);")

        # ADR-0031: shadow would-send evaluation annotations
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS shadow_annotations (
                id TEXT PRIMARY KEY,
                stimulus_id TEXT,
                scene_id TEXT NOT NULL,
                label TEXT NOT NULL,
                comment TEXT,
                created_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_shadow_annotations_scene ON shadow_annotations(scene_id, created_at);")

        # ADR-0038 §5: curated bot voice exemplars (scene_id='' means global)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS voice_exemplars (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                tag TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_voice_exemplars_scene ON voice_exemplars(scene_id, enabled);")

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

    async def save_trace(self, kind: str, scene_id: str, ref_id: str, payload: dict[str, Any]) -> str:
        """ADR-0022: append a behavior trace row (attention evaluation or episode chain)."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        import uuid
        trace_id = f"trc_{uuid.uuid4().hex[:12]}"
        async with self._write_lock:
            await self._db.execute(
                "INSERT INTO traces (id, kind, scene_id, ref_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?);",
                (trace_id, kind, scene_id, ref_id, json.dumps(payload, ensure_ascii=False, default=str), time.time())
            )
            await self._db.commit()
        return trace_id

    async def query_traces(
        self,
        scene_id: Optional[str] = None,
        kind: Optional[str] = None,
        ref_id: Optional[str] = None,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        sql = "SELECT id, kind, scene_id, ref_id, payload, created_at FROM traces WHERE 1=1"
        params: list[Any] = []
        if scene_id:
            sql += " AND scene_id = ?"
            params.append(scene_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if ref_id:
            sql += " AND ref_id = ?"
            params.append(ref_id)
        sql += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "kind": r[1], "scene_id": r[2], "ref_id": r[3], "payload": json.loads(r[4]), "created_at": r[5]}
            for r in rows
        ]

    async def save_shadow_annotation(self, annotation: dict[str, Any]) -> dict[str, Any]:
        """ADR-0031, §23.3: Persists a shadow would-send evaluation annotation."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        import uuid
        ann_id = annotation.get("id") or f"sa_{uuid.uuid4().hex[:10]}"
        now = annotation.get("created_at", time.time())
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO shadow_annotations (id, stimulus_id, scene_id, label, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    comment = excluded.comment;
                """,
                (
                    ann_id,
                    annotation.get("stimulus_id"),
                    annotation["scene_id"],
                    annotation["label"],
                    annotation.get("comment", ""),
                    now
                )
            )
            await self._db.commit()
        return {
            "id": ann_id,
            "stimulus_id": annotation.get("stimulus_id"),
            "scene_id": annotation["scene_id"],
            "label": annotation["label"],
            "comment": annotation.get("comment", ""),
            "created_at": now
        }

    async def get_shadow_annotations(self, scene_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        """ADR-0031, §23.3: Returns shadow would-send annotations."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        sql = "SELECT id, stimulus_id, scene_id, label, comment, created_at FROM shadow_annotations"
        params: list[Any] = []
        if scene_id:
            sql += " WHERE scene_id = ?"
            params.append(scene_id)
        sql += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "stimulus_id": r[1],
                "scene_id": r[2],
                "label": r[3],
                "comment": r[4],
                "created_at": r[5]
            }
            for r in rows
        ]

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
        """Return the latest scene events in their authoritative commit order."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        
        cursor = await self._db.execute(
            """
            SELECT id, event_type, scene_id, actor_id, timestamp, payload, metadata
            FROM (
                SELECT rowid, id, event_type, scene_id, actor_id, timestamp, payload, metadata
                FROM events
                WHERE scene_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            )
            ORDER BY rowid ASC;
            """,
            (scene_id, limit)
        )
        rows = await cursor.fetchall()
        events = []
        for r in rows:
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

    async def get_events_since(self, scene_id: str, after_rowid: int = 0, limit: int = 200) -> list[Event]:
        """ADR-0019 §10.4: events after a reflection cursor, in immutable write order.
        Each event's metadata carries its `_rowid` so callers can advance the cursor."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT rowid, id, event_type, scene_id, actor_id, timestamp, payload, metadata
            FROM events
            WHERE scene_id = ? AND rowid > ?
            ORDER BY rowid ASC
            LIMIT ?;
            """,
            (scene_id, after_rowid, limit)
        )
        rows = await cursor.fetchall()
        events = []
        for r in rows:
            metadata = json.loads(r[7])
            metadata["_rowid"] = r[0]
            events.append(Event(
                id=r[1],
                event_type=EventType(r[2]),
                scene_id=r[3],
                actor_id=r[4],
                timestamp=r[5],
                payload=json.loads(r[6]),
                metadata=metadata
            ))
        return events

    async def get_unreflected_events(self, scene_id: str, after_rowid: int = 0, limit: int = 30) -> list[Event]:
        """ADR-0028, §10.1: retrieves unreflected events bounded strictly to batch limit (default 30).
        Events are in immutable write order with `_rowid` attached to metadata."""
        return await self.get_events_since(scene_id, after_rowid=after_rowid, limit=limit)

    async def commit_reflection_batch(
        self,
        scene_id: str,
        episode_record: EpisodeRecord,
        proposals: list[MemoryProposal],
        new_cursor_rowid: int,
    ) -> tuple[EpisodeRecord, list[MemoryItem]]:
        """ADR-0028, §10.2: Atomic Reflection Batch Commit.
        Executes within a single SQLite transaction under _write_lock:
        1. INSERT EpisodeRecord into episodes.
        2. Validate & commit each MemoryProposal via validate_memory_proposal & commit_memory_proposal_core
           with forced scope = scene_id.
        3. Upsert reflection_cursors with new_cursor_rowid.
        Any failure rolls back the entire batch: no episode, no memories, and cursor remains unchanged.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        async with self._write_lock:
            try:
                # 1. Insert EpisodeRecord
                await self._db.execute(
                    """
                    INSERT INTO episodes (id, scene_id, title, summary, source_event_ids, participants, tags, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        episode_record.id,
                        episode_record.scene_id,
                        episode_record.title,
                        episode_record.summary,
                        json.dumps(episode_record.source_event_ids, ensure_ascii=False),
                        json.dumps(episode_record.participants, ensure_ascii=False),
                        json.dumps(episode_record.tags, ensure_ascii=False),
                        episode_record.created_at,
                    ),
                )

                # 2. Validate and commit all MemoryProposals
                committed_memories: list[MemoryItem] = []
                for mp in proposals:
                    await validate_memory_proposal(self._db, mp, scene_id)
                    mem_item = await commit_memory_proposal_core(self._db, mp, scene_id)
                    committed_memories.append(mem_item)

                # 3. Advance reflection_cursors
                now = time.time()
                await self._db.execute(
                    """
                    INSERT INTO reflection_cursors (scene_id, last_event_rowid, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(scene_id) DO UPDATE SET
                        last_event_rowid = excluded.last_event_rowid,
                        updated_at = excluded.updated_at;
                    """,
                    (scene_id, new_cursor_rowid, now),
                )

                await self._db.commit()
                return episode_record, committed_memories
            except Exception as e:
                await self._db.rollback()
                raise ValueError(f"Failed to commit reflection batch for scene {scene_id}, transaction rolled back: {e}") from e

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

    async def references_belong_to_scene(
        self,
        reference_ids: set[str],
        scene_id: str,
    ) -> bool:
        """Validate retrieved event/episode references against the SQL scope boundary."""
        if not reference_ids:
            return True
        if not self._db:
            raise RuntimeError("Database not initialized")

        ids = list(reference_ids)
        placeholders = ",".join("?" for _ in ids)
        event_cursor = await self._db.execute(
            f"SELECT id FROM events WHERE scene_id = ? AND id IN ({placeholders})",
            [scene_id, *ids],
        )
        episode_cursor = await self._db.execute(
            f"SELECT id FROM episodes WHERE scene_id = ? AND id IN ({placeholders})",
            [scene_id, *ids],
        )
        found = {row[0] for row in await event_cursor.fetchall()}
        found.update(row[0] for row in await episode_cursor.fetchall())
        return found == reference_ids

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

    async def load_group_agent_session(self, scene_id: str) -> Optional[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT version, last_observed_event_rowid, last_cognized_event_rowid, state_json
            FROM group_agent_sessions
            WHERE scene_id = ?;
            """,
            (scene_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data = json.loads(row[3])
        data["version"] = row[0]
        data["last_observed_event_rowid"] = row[1]
        data["last_cognized_event_rowid"] = row[2]
        return data

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
        associated_open_loop: Optional[dict[str, Any]] = None,
        group_session_data: Optional[dict[str, Any]] = None,
        advance_session_observation: bool = True,
    ) -> int:
        """Atomically commit an Event and its materialized scene state."""
        if not self._db:
            raise RuntimeError("Database not initialized")

        async with self._write_lock:
            try:
                payload_str = json.dumps(event.payload, ensure_ascii=False)
                metadata_str = json.dumps(event.metadata, ensure_ascii=False)

                # 1. Insert Event
                event_cursor = await self._db.execute(
                    """
                    INSERT INTO events (id, event_type, scene_id, actor_id, timestamp, payload, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp, payload_str, metadata_str)
                )
                event_rowid = int(event_cursor.lastrowid)

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
                        INSERT INTO open_loops (id, scene_id, target_actor_id, intent, source_event_id, source_stimulus_id, status, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET status = excluded.status;
                        """,
                        (
                            associated_open_loop["id"],
                            associated_open_loop["scene_id"],
                            associated_open_loop["target_actor_id"],
                            associated_open_loop["intent"],
                            event.id,
                            associated_open_loop.get("source_stimulus_id"),
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

                if group_session_data is not None:
                    persisted_session = dict(group_session_data)
                    if advance_session_observation:
                        persisted_session["last_observed_event_rowid"] = event_rowid
                    session_version = int(persisted_session.get("version", 0))
                    last_observed = int(persisted_session.get("last_observed_event_rowid", 0))
                    last_cognized = int(persisted_session.get("last_cognized_event_rowid", 0))
                    await self._db.execute(
                        """
                        INSERT INTO group_agent_sessions (
                            scene_id, version, last_observed_event_rowid,
                            last_cognized_event_rowid, state_json, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(scene_id) DO UPDATE SET
                            version = excluded.version,
                            last_observed_event_rowid = excluded.last_observed_event_rowid,
                            last_cognized_event_rowid = excluded.last_cognized_event_rowid,
                            state_json = excluded.state_json,
                            updated_at = excluded.updated_at;
                        """,
                        (
                            event.scene_id,
                            session_version,
                            last_observed,
                            last_cognized,
                            json.dumps(persisted_session, ensure_ascii=False),
                            time.time(),
                        ),
                    )

                await self._db.commit()
                return event_rowid
            except Exception:
                await self._db.rollback()
                raise

    async def get_active_open_loops(self, scene_id: str) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT id, scene_id, target_actor_id, intent, source_event_id, source_stimulus_id, status, created_at, expires_at
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
                "source_stimulus_id": r[5],
                "status": r[6],
                "created_at": r[7],
                "expires_at": r[8],
            }
            for r in rows
        ]

    async def save_open_loop(self, loop_data: dict[str, Any]) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO open_loops (id, scene_id, target_actor_id, intent, source_event_id, source_stimulus_id, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status;
                """,
                (
                    loop_data["id"],
                    loop_data["scene_id"],
                    loop_data["target_actor_id"],
                    loop_data["intent"],
                    loop_data["source_event_id"],
                    loop_data.get("source_stimulus_id"),
                    loop_data["status"],
                    loop_data["created_at"],
                    loop_data["expires_at"]
                )
            )
            await self._db.commit()

    # ------------------------------------------------------------------
    # ADR-0038 §5: curated bot voice exemplars
    # ------------------------------------------------------------------

    async def add_voice_example(
        self,
        scene_id: str,
        content: str,
        context: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        example_id = f"voice_{uuid.uuid4().hex[:10]}"
        now = time.time()
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO voice_exemplars (id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, 0, ?);
                """,
                (example_id, scene_id or "", context, content, tag, now),
            )
            await self._db.commit()
        return {
            "id": example_id,
            "scene_id": scene_id or "",
            "context": context,
            "content": content,
            "tag": tag,
            "enabled": True,
            "use_count": 0,
            "last_used_at": 0.0,
            "created_at": now,
        }

    async def list_voice_examples(self, scene_id: str | None = None) -> list[dict[str, Any]]:
        """All exemplars; when scene_id is given, that scene's plus global ones."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if scene_id is None:
            cursor = await self._db.execute(
                "SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at FROM voice_exemplars ORDER BY created_at DESC;"
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at
                FROM voice_exemplars
                WHERE scene_id IN (?, '')
                ORDER BY created_at DESC;
                """,
                (scene_id,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "scene_id": r[1],
                "context": r[2],
                "content": r[3],
                "tag": r[4],
                "enabled": bool(r[5]),
                "use_count": r[6],
                "last_used_at": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    async def delete_voice_example(self, example_id: str) -> bool:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            cursor = await self._db.execute(
                "DELETE FROM voice_exemplars WHERE id = ?;",
                (example_id,),
            )
            await self._db.commit()
        return cursor.rowcount > 0

    async def set_voice_example_enabled(self, example_id: str, enabled: bool) -> bool:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            cursor = await self._db.execute(
                "UPDATE voice_exemplars SET enabled = ? WHERE id = ?;",
                (1 if enabled else 0, example_id),
            )
            await self._db.commit()
        return cursor.rowcount > 0

    async def select_voice_examples(self, scene_id: str, limit: int) -> list[dict[str, Any]]:
        """ADR-0038 §5: LRU rotation — least-recently-used enabled exemplars first.

        Bumps use_count/last_used_at for the picked rows so the same batch is
        not injected repeatedly.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")
        if limit <= 0:
            return []
        cursor = await self._db.execute(
            """
            SELECT id, scene_id, context, content, tag FROM voice_exemplars
            WHERE enabled = 1 AND scene_id IN (?, '')
            ORDER BY last_used_at ASC, use_count ASC, created_at DESC
            LIMIT ?;
            """,
            (scene_id, limit),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        now = time.time()
        picked = [
            {"id": r[0], "scene_id": r[1], "context": r[2], "content": r[3], "tag": r[4]}
            for r in rows
        ]
        async with self._write_lock:
            await self._db.executemany(
                "UPDATE voice_exemplars SET use_count = use_count + 1, last_used_at = ? WHERE id = ?;",
                [(now, item["id"]) for item in picked],
            )
            await self._db.commit()
        return picked

    async def create_task(self, task_data: dict[str, Any]) -> None:
        """P0.1: Dedicated write authority for tasks under write_lock."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            wake_match = task_data.get("wake_match")
            wake_match_json = json.dumps(wake_match, ensure_ascii=False) if wake_match else None
            await self._db.execute(
                """
                INSERT INTO tasks (
                    id, scene_id, description, due_at, status, source_event_id,
                    payload, created_at, wake_event_type, wake_match_json,
                    origin_episode_id, origin_stimulus_id, trigger_event_id, origin_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_data["id"],
                    task_data["scene_id"],
                    task_data["description"],
                    task_data["due_at"],
                    task_data["status"],
                    task_data["source_event_id"],
                    json.dumps(task_data.get("payload", {}), ensure_ascii=False) if not isinstance(task_data.get("payload"), str) else task_data.get("payload"),
                    task_data["created_at"],
                    task_data.get("wake_event_type"),
                    wake_match_json,
                    task_data.get("origin_episode_id"),
                    task_data.get("origin_stimulus_id"),
                    task_data.get("trigger_event_id"),
                    task_data.get("origin_mode", "live"),
                )
            )
            await self._db.commit()

    async def save_task(self, task: Any) -> None:
        """Saves a TaskItem directly into tasks table."""
        status_val = task.status.value if hasattr(task.status, "value") else str(task.status)
        await self.create_task({
            "id": task.id,
            "scene_id": task.scene_id,
            "description": task.description,
            "due_at": task.due_at,
            "status": status_val,
            "source_event_id": getattr(task, "source_event_id", "manual"),
            "payload": getattr(task, "payload", {}),
            "created_at": getattr(task, "created_at", time.time()),
            "wake_event_type": getattr(task, "wake_event_type", None),
            "wake_match": getattr(task, "wake_match", None),
            "origin_episode_id": getattr(task, "origin_episode_id", None),
            "origin_stimulus_id": getattr(task, "origin_stimulus_id", None),
            "trigger_event_id": getattr(task, "trigger_event_id", None),
            "origin_mode": getattr(task, "origin_mode", "live"),
        })

    async def claim_task(self, task_id: str, trigger_event_id: str = "") -> bool:
        """ADR-0029, §15: Durable Task Claim.
        Preemptively transitions task status from 'pending' to 'claimed' with trigger_event_id.
        Returns True ONLY if this caller successfully claimed the task (rowcount == 1).
        """
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            cursor = await self._db.execute(
                """
                UPDATE tasks
                SET status = 'claimed', trigger_event_id = ?
                WHERE id = ? AND status = 'pending';
                """,
                (trigger_event_id, task_id),
            )
            await self._db.commit()
            return cursor.rowcount > 0

    async def get_pending_tasks(self, max_due_at: Optional[float] = None) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        sql = """
            SELECT id, scene_id, description, due_at, status, source_event_id,
                   payload, created_at, wake_event_type, wake_match_json,
                   origin_episode_id, origin_stimulus_id, trigger_event_id, origin_mode
            FROM tasks WHERE status = 'pending'
        """
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

        def _parse_json(raw: Any) -> Any:
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return None

        return [
            {
                "id": r[0],
                "scene_id": r[1],
                "description": r[2],
                "due_at": r[3],
                "status": r[4],
                "source_event_id": r[5],
                "payload": _parse_payload(r[6]),
                "created_at": r[7],
                "wake_event_type": r[8],
                "wake_match": _parse_json(r[9]),
                "origin_episode_id": r[10] if len(r) > 10 else None,
                "origin_stimulus_id": r[11] if len(r) > 11 else None,
                "trigger_event_id": r[12] if len(r) > 12 else None,
                "origin_mode": r[13] if len(r) > 13 and r[13] else "live",
            }
            for r in rows
        ]

    async def get_pending_next_wake(self, scene_id: str) -> Optional[dict[str, Any]]:
        """Return the scene's authoritative pending ambient wake, if any."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT id, description, due_at, payload, created_at, origin_mode
            FROM tasks
            WHERE scene_id = ? AND status = 'pending'
              AND json_extract(payload, '$.kind') = 'next_wake'
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (scene_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(row[3])
        return {
            "task_id": row[0],
            "reason": payload["reason"],
            "wake_at": row[2],
            "source_event_ids": payload.get("source_event_ids", []),
            "created_at": row[4],
            "origin_mode": row[5] or "live",
        }

    async def mark_task_status(self, task_id: str, status: str, trigger_event_id: Optional[str] = None) -> None:
        if not self._db:
            raise RuntimeError("Database not initialized")
        async with self._write_lock:
            if trigger_event_id is not None:
                await self._db.execute(
                    "UPDATE tasks SET status = ?, trigger_event_id = ? WHERE id = ?;",
                    (status, trigger_event_id, task_id)
                )
            else:
                await self._db.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?;",
                    (status, task_id)
                )
            await self._db.commit()
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

    async def commit_proposal_transaction(
        self,
        episode_id: str,
        scene_id: str,
        task_proposals: list[Any],
        resolve_open_loop_ids: list[str],
        memory_proposals: list[Any],
    ) -> tuple[list[Any], list[str], list[Any]]:
        """
        V2 Atomic Proposal Commit (ADR-0003 & ADR-0011 Closure):
        Executes an all-or-nothing atomic durable commit for an EpisodeOutcome
        within a single SQLite transaction under write_lock.
        If any mutation fails, the entire transaction is rolled back.
        Returns: (committed_tasks, resolved_loop_ids, committed_memories)
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        import uuid
        from len_bot.scheduler.models import TaskItem, TaskStatus
        from len_bot.memory.models import MemoryItem, MemoryCertainty, MemoryStatus
        from len_bot.cognition.models import CONDITION_TASK_DEFAULT_DEADLINE_SECONDS

        now = time.time()
        committed_tasks: list[TaskItem] = []
        resolved_loop_ids: list[str] = []
        committed_memories: list[MemoryItem] = []

        async with self._write_lock:
            try:
                # 1. Validate evidence integrity for all memory proposals upfront
                for mp in memory_proposals:
                    mp.scope = scene_id
                    if not mp.evidence:
                        raise ValueError(f"Memory proposal '{mp.key}' lacks evidence references")

                    placeholders = ",".join("?" for _ in mp.evidence)
                    cursor = await self._db.execute(
                        f"SELECT COUNT(*) FROM events WHERE id IN ({placeholders}) AND scene_id = ?;",
                        [*mp.evidence, scene_id]
                    )
                    (ev_count,) = await cursor.fetchone()

                    ep_cursor = await self._db.execute(
                        f"SELECT COUNT(*) FROM episodes WHERE id IN ({placeholders}) AND scene_id = ?;",
                        [*mp.evidence, scene_id]
                    )
                    (ep_count,) = await ep_cursor.fetchone()

                    required_evidence_count = len(set(mp.evidence))
                    if (ev_count + ep_count) < required_evidence_count:
                        raise ValueError(
                            f"Evidence integrity check failed for memory '{mp.key}'. "
                            f"Expected {required_evidence_count} evidence items in scope '{scene_id}', found {ev_count + ep_count}"
                        )

                # 2. Insert Tasks
                for tp in task_proposals:
                    task_id = f"task_{uuid.uuid4().hex[:10]}"
                    payload_json = json.dumps(tp.payload, ensure_ascii=False)
                    # ADR-0034: at most one pending next-wake task per scene; a new
                    # one durably supersedes the previous inside the same transaction.
                    if tp.payload.get("kind") == "next_wake":
                        supersede_cursor = await self._db.execute(
                            """
                            UPDATE tasks SET status = 'cancelled'
                            WHERE scene_id = ? AND status = 'pending'
                              AND json_extract(payload, '$.kind') = 'next_wake';
                            """,
                            (scene_id,)
                        )
                        if supersede_cursor.rowcount:
                            logger.info(
                                "Next-wake supersede on scene %s: cancelled %d prior pending task(s)",
                                scene_id, supersede_cursor.rowcount
                            )
                    wake_match_json = json.dumps(tp.wake_match, ensure_ascii=False) if getattr(tp, "wake_match", None) else None
                    # ADR-0018: condition-bound tasks fire on wake_event_type or deadline, whichever first.
                    delay = tp.delay_seconds if tp.delay_seconds is not None else CONDITION_TASK_DEFAULT_DEADLINE_SECONDS
                    await self._db.execute(
                        """
                        INSERT INTO tasks (
                            id, scene_id, description, due_at, status, source_event_id,
                            payload, created_at, wake_event_type, wake_match_json,
                            origin_episode_id, origin_stimulus_id, origin_mode
                        )
                        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            task_id, scene_id, tp.description, now + delay, episode_id,
                            payload_json, now, tp.wake_event_type, wake_match_json,
                            episode_id, getattr(tp, "origin_stimulus_id", None),
                            getattr(tp, "origin_mode", "live")
                        )
                    )
                    task_item = TaskItem(
                        id=task_id,
                        scene_id=scene_id,
                        description=tp.description,
                        due_at=now + delay,
                        status=TaskStatus.PENDING,
                        payload=tp.payload,
                        source_event_id=episode_id,
                        created_at=now,
                        wake_event_type=tp.wake_event_type,
                        wake_match=getattr(tp, "wake_match", None),
                        origin_episode_id=episode_id,
                        origin_stimulus_id=getattr(tp, "origin_stimulus_id", None),
                        origin_mode=getattr(tp, "origin_mode", "live")
                    )
                    committed_tasks.append(task_item)

                # 3. Resolve Open Loops
                # 3. Resolve Open Loops (ADR-0025: strictly scoped to this scene)
                for loop_id in resolve_open_loop_ids:
                    cursor = await self._db.execute(
                        "UPDATE open_loops SET status = 'resolved' WHERE id = ? AND scene_id = ? AND status = 'active';",
                        (loop_id, scene_id)
                    )
                    if cursor.rowcount == 0:
                        raise ValueError(f"Cannot resolve open loop '{loop_id}': not active or does not belong to scene '{scene_id}'")
                    resolved_loop_ids.append(loop_id)

                # 4. Commit Memory Proposals with Unified Validation & Conflict Resolution (ADR-0025, §13)
                for mp in memory_proposals:
                    await validate_memory_proposal(self._db, mp, scene_id)
                    mem_item = await commit_memory_proposal_core(self._db, mp, scene_id, now=now)
                    committed_memories.append(mem_item)

                # 5. Commit all mutations atomically in one transaction!
                await self._db.commit()
                return committed_tasks, resolved_loop_ids, committed_memories
            except Exception:
                await self._db.rollback()
                raise
