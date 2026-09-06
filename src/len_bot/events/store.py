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
from len_bot.tools.observations import ObservationStoreMixin
from len_bot.runtime.job_store import JobStoreMixin
from len_bot.media.store import MediaStoreMixin

logger = logging.getLogger(__name__)

class ReflectionConflictError(ValueError):
    """A newer cursor or understanding requires a fresh quiet-window read."""


class EventStore(ObservationStoreMixin, JobStoreMixin, MediaStoreMixin):
    def __init__(self, db_path: str = "len_bot.db", clock=time.time):
        self.clock = clock
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self.initialize_observations()
        await self.initialize_jobs()
        await self.initialize_media()
        
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

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS pending_runtime_events (
                id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, event_json TEXT NOT NULL
            )
        """)
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
        await self._db.execute("UPDATE tasks SET status='review_required' WHERE status='triggered'")
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
        voice_columns = await (await self._db.execute("PRAGMA table_info(voice_exemplars)")).fetchall()
        if "source" not in {row[1] for row in voice_columns}:
            await self._db.execute("ALTER TABLE voice_exemplars ADD COLUMN source TEXT NOT NULL DEFAULT 'operator'")

        await self._db.commit()

        await self._remove_evaluation_storage()
        await self._migrate_person_names()

    async def _remove_evaluation_storage(self) -> None:
        """Retire evaluation projections once, without touching raw scene history."""
        if await self.get_dynamic_config("evaluation_removed_v1") is not None:
            return
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                for table in ("rollout_evaluations", "rollout_candidate_reviews",
                              "reply_followups", "reply_feedback_labels",
                              "reply_observations", "shadow_annotations"):
                    await self._db.execute(f"DROP TABLE IF EXISTS {table}")
                await self._db.execute(
                    "DELETE FROM runtime_dynamic_configs WHERE key IN ('delivery_policy','vision_acceptance')")
                await self._db.execute(
                    "INSERT INTO runtime_dynamic_configs VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
                    ("delivery_scenes", '{"scene_ids":["group:126300994"]}', self.clock()))
                await self._db.execute(
                    "INSERT INTO runtime_dynamic_configs VALUES(?,?,?)",
                    ("evaluation_removed_v1", '{"applied":true}', self.clock()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def _migrate_person_names(self) -> None:
        """One-time factual rebuild before actors start; never infer preferred names."""
        if await self.get_dynamic_config("person_names_v2") is not None:
            return
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                rows = await (await self._db.execute(
                    "SELECT scene_id,state_json FROM group_agent_sessions")).fetchall()
                for scene_id, raw in rows:
                    data = json.loads(raw)
                    for actor_id, person in data.get("working_persons", {}).items():
                        for field in ("nickname", "card"):
                            source = await (await self._db.execute(
                                "SELECT json_extract(payload, ?) FROM events WHERE scene_id=? AND actor_id=? "
                                "AND json_type(payload, ?)='text' ORDER BY rowid DESC LIMIT 1",
                                (f"$.sender.{field}", scene_id, actor_id, f"$.sender.{field}"),
                            )).fetchone()
                            person[field] = source[0] if source else None
                    await self._db.execute("UPDATE group_agent_sessions SET state_json=? WHERE scene_id=?",
                                           (json.dumps(data, ensure_ascii=False), scene_id))
                await self._db.execute("INSERT INTO runtime_dynamic_configs VALUES(?,?,?)",
                                       ("person_names_v2", '{"applied":true}', self.clock()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def reset_conversation_data(self, event: Event) -> dict[str, int]:
        """Explicit operator reset; configuration and authored voice stay intact."""
        tables = ("events_fts", "pending_runtime_events", "agent_jobs", "tool_observations",
                  "media_assets", "traces", "reflection_cursors", "memories", "episodes",
                  "open_loops", "tasks", "group_agent_sessions", "scene_states", "events")
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                counts = {}
                for table in tables:
                    where = " WHERE curated=0" if table == "media_assets" else (
                        " WHERE id NOT IN (SELECT source_event_id FROM media_assets) AND "
                        "NOT (event_type='MEDIA_UPDATED' AND COALESCE(json_extract(payload,'$.asset_id') "
                        "IN (SELECT id FROM media_assets),0))" if table == "events" else "")
                    counts[table] = (await (await self._db.execute(f"SELECT COUNT(*) FROM {table}{where}")).fetchone())[0]
                    await self._db.execute(f"DELETE FROM {table}{where}")
                await self._db.execute("UPDATE voice_exemplars SET use_count=0,last_used_at=0")
                await self._db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
                    (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp,
                     json.dumps(event.payload), json.dumps(event.metadata)))
                await self._db.commit()
                return counts
            except BaseException:
                await self._db.rollback()
                raise

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
        now = self.clock()
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
        now = self.clock()
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

    async def project_reply_context(self, scene_id: str, events: list[Event]) -> list[Event]:
        """Attach read-only, scene-scoped quote facts to copies, never raw history."""
        refs = {str(e.payload["reply_to_message_id"]) for e in events if e.payload.get("reply_to_message_id") is not None}
        if not refs:
            return events
        cursor = await self._db.execute(
            "SELECT id, actor_id, payload,metadata FROM events WHERE scene_id=? AND "
            "CAST(json_extract(payload, '$.message_id') AS TEXT) IN (" + ",".join("?" for _ in refs) + ")",
            (scene_id, *sorted(refs)),
        )
        quotes = {}
        for event_id, actor_id, payload_json, metadata_json in await cursor.fetchall():
            payload = json.loads(payload_json)
            quotes[str(payload["message_id"])] = {
                "event_id": event_id, "actor_id": actor_id,
                "text": payload.get("raw_text") or payload.get("content", ""),
                "media": json.loads(metadata_json).get("media", []),
            }
        projected = []
        for event in events:
            item = event.model_copy(deep=True)
            ref = item.payload.get("reply_to_message_id")
            if ref is not None:
                item.metadata["quote_context"] = quotes.get(str(ref), {"missing": True})
            projected.append(item)
        return projected

    async def own_sent_message_ids(self, actor_id: str, limit: int = 1000) -> list[str]:
        cursor = await self._db.execute(
            "SELECT json_extract(payload, '$.message_id') FROM events "
            "WHERE event_type = ? AND actor_id = ? AND json_extract(payload, '$.message_id') IS NOT NULL "
            "ORDER BY rowid DESC LIMIT ?", (EventType.MESSAGE_SENT.value, actor_id, limit),
        )
        return [str(row[0]) for row in reversed(await cursor.fetchall())]

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

    async def get_events_since(self, scene_id: str, after_rowid: int = 0, limit: int = 200, event_types: list[EventType] | None = None) -> list[Event]:
        """ADR-0019 §10.4: events after a reflection cursor, in immutable write order.
        Each event's metadata carries its `_rowid` so callers can advance the cursor."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        type_filter = ""
        params = [scene_id, after_rowid]
        if event_types:
            type_filter = " AND event_type IN (" + ",".join("?" for _ in event_types) + ")"
            params.extend(t.value for t in event_types)
        cursor = await self._db.execute(
            f"""
            SELECT rowid, id, event_type, scene_id, actor_id, timestamp, payload, metadata
            FROM events
            WHERE scene_id = ? AND rowid > ? {type_filter}
            ORDER BY rowid ASC
            LIMIT ?;
            """,
            [*params, limit]
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
        return await self.get_events_since(scene_id, after_rowid=after_rowid, limit=limit,
            event_types=[EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
                         EventType.MESSAGE_SENT, EventType.LIVE_STARTED, EventType.LIVE_ENDED,
                         EventType.USER_JOINED, EventType.MESSAGE_SEND_FAILED])

    async def commit_reflection_batch(
        self,
        scene_id: str,
        episode_record: EpisodeRecord,
        proposals: list[MemoryProposal],
        new_cursor_rowid: int,
        review_event: Event | None = None,
        expected_cursor_rowid: int | None = None,
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
                await self._db.execute("BEGIN IMMEDIATE")
                if review_event is not None and "social_revision" in review_event.payload:
                    state = await (await self._db.execute(
                        "SELECT json_extract(state_json,'$.social_revision') FROM group_agent_sessions WHERE scene_id=?",
                        (scene_id,))).fetchone()
                    if state is not None and (state[0] or 0) != review_event.payload["social_revision"]:
                        raise ReflectionConflictError("Reflection understanding changed before commit; reread current state")
                if episode_record.scene_id != scene_id or not episode_record.source_event_ids:
                    raise ValueError("Reflection episode must have same-scene source evidence")
                source_ids = set(episode_record.source_event_ids)
                placeholders = ",".join("?" for _ in source_ids)
                evidence = await self._db.execute(
                    f"SELECT id,rowid FROM events WHERE scene_id=? AND id IN ({placeholders})",
                    [scene_id, *source_ids])
                observed = await evidence.fetchall()
                if len(observed) != len(source_ids) or new_cursor_rowid != max(row[1] for row in observed):
                    raise ValueError("Reflection cursor must end at its real source evidence")
                if expected_cursor_rowid is not None:
                    cursor = await self._db.execute("SELECT last_event_rowid FROM reflection_cursors WHERE scene_id=?", (scene_id,))
                    current = await cursor.fetchone()
                    if (current[0] if current else 0) != expected_cursor_rowid:
                        raise ReflectionConflictError("Reflection cursor changed during inference")
                if review_event is not None:
                    if review_event.scene_id != scene_id or review_event.event_type != EventType.REFLECTION_RECORDED:
                        raise ValueError("Invalid reflection envelope")
                    for item in review_event.payload.get("review_items", []):
                        if not item["source_event_ids"] or not set(item["source_event_ids"]).issubset(source_ids):
                            raise ValueError("Review item lacks reflected evidence")
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

                if review_event is not None:
                    review_event.payload["memory_receipts"] = [
                        await self._memory_receipt(mp, item) for mp, item in zip(proposals, committed_memories)
                    ]
                    await self._db.execute(
                        "INSERT INTO pending_runtime_events VALUES (?, ?, ?)",
                        (review_event.id, scene_id, review_event.model_dump_json()),
                    )

                # 3. Advance reflection_cursors
                now = self.clock()
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
            except ReflectionConflictError:
                await self._db.rollback()
                raise
            except Exception as e:
                await self._db.rollback()
                raise ValueError(f"Failed to commit reflection batch for scene {scene_id}, transaction rolled back: {e}") from e
            except BaseException:
                await self._db.rollback()
                raise

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
        through_event_rowid: int | None = None,
    ) -> bool:
        """Validate retrieved event/episode references against the SQL scope boundary."""
        if not reference_ids:
            return True
        if not self._db:
            raise RuntimeError("Database not initialized")

        ids = list(reference_ids)
        placeholders = ",".join("?" for _ in ids)
        event_cursor = await self._db.execute(
            f"SELECT id FROM events WHERE scene_id = ? AND id IN ({placeholders}) AND (? IS NULL OR rowid<=?)",
            [scene_id, *ids, through_event_rowid, through_event_rowid],
        )
        found = {row[0] for row in await event_cursor.fetchall()}
        if found == reference_ids:
            return True
        episode_cursor = await self._db.execute(
            f"""SELECT id FROM episodes WHERE scene_id = ? AND id IN ({placeholders})
                AND NOT EXISTS (SELECT 1 FROM json_each(source_event_ids) AS source
                    WHERE NOT EXISTS (SELECT 1 FROM events WHERE events.id=source.value
                        AND events.scene_id=episodes.scene_id AND (? IS NULL OR events.rowid<=?)))""",
            [scene_id, *ids, through_event_rowid, through_event_rowid],
        )
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
        async with self._write_lock:
            try:
                rowid = await self._write_scene_event(
                    event, scene_state_data, task_id_to_trigger, associated_open_loop,
                    group_session_data, advance_session_observation,
                )
                await self._db.commit()
                return rowid
            except BaseException:
                await self._db.rollback()
                raise

    async def _write_scene_event(
        self,
        event: Event,
        scene_state_data: dict[str, Any],
        task_id_to_trigger: Optional[str] = None,
        associated_open_loop: Optional[dict[str, Any]] = None,
        group_session_data: Optional[dict[str, Any]] = None,
        advance_session_observation: bool = True,
    ) -> int:
        """Write inside the caller's locked transaction; no commit authority here."""
        await self.register_event_media_in_transaction(event)
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
                "UPDATE tasks SET status = ? WHERE id = ? AND scene_id = ? AND status = 'claimed';",
                ("processing", task_id_to_trigger, event.scene_id)
            )

        await self._db.execute("DELETE FROM pending_runtime_events WHERE id=? AND scene_id=?",
                               (event.id, event.scene_id))
        task_id = event.payload.get("fulfils_task_id")
        if task_id and event.event_type in (EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED):
            status = ("shadow_observed" if event.event_type == EventType.ACTION_SHADOWED else "completed" if event.event_type == EventType.MESSAGE_SENT
                      else "delivery_unknown" if event.payload.get("delivery_unknown") else "failed")
            await self._db.execute(
                """UPDATE tasks SET status=?,payload=json_set(payload,'$.delivery_event_id',?,'$.error',?) WHERE id=? AND scene_id=?
                   AND status='awaiting_delivery'
                   AND json_extract(payload, '$.delivery_action_id')=?""",
                (status, event.id, event.payload.get("error", ""), task_id, event.scene_id, event.payload.get("action_id")),
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

        return event_rowid

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

    async def preview_diana_persona(self) -> dict:
        import hashlib
        from len_bot.cognition.diana import PERSONA, PRESET_ID, LEGACY_PRESET_ID, LEGACY_PERSONA, LEGACY_EXAMPLES, EXAMPLES, EXAMPLE_IDS
        current = await self.get_dynamic_config("persona_config") or {}
        applied = await self.get_dynamic_config(PRESET_ID) is not None
        examples = await self.list_voice_examples()
        fields = []
        for key, desired in PERSONA.items():
            previous = current.get(key)
            update = not applied and (previous is None or previous == LEGACY_PERSONA[key])
            fields.append({"key": key, "current": previous, "next": desired if update else previous,
                           "action": "update" if update and previous != desired else
                                     "preserve" if previous != desired else "unchanged"})
        legacy = {f"{LEGACY_PRESET_ID}:{i}": (context, content)
                  for i, (context, content) in enumerate(LEGACY_EXAMPLES)}
        disable = [item["id"] for item in examples if item["id"] in legacy
                   and (item["context"], item["content"]) == legacy[item["id"]]]
        token = hashlib.sha256(json.dumps([current, examples, applied], sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()
        return {"preset_id": PRESET_ID, "applied": applied, "fields": fields,
                "disable_example_ids": disable, "example_count": sum(eid not in {item["id"] for item in examples} for eid in EXAMPLE_IDS), "examples": [{"id": eid, "context": value[0], "content": value[1]} for eid, value in zip(EXAMPLE_IDS, EXAMPLES)], "preview_token": token}

    async def apply_diana_persona(self, bot_qq: int, expected_token: str | None = None) -> bool:
        """Explicit, atomic preset migration. Preserve edits; never run on startup."""
        from len_bot.cognition.diana import EXAMPLES, PRESET_ID, EXAMPLE_IDS
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                preview = await self.preview_diana_persona()
                if expected_token is not None and expected_token != preview["preview_token"]:
                    raise ValueError("配置已变化，请重新预览后应用")
                if preview["applied"]:
                    await self._db.rollback()
                    return False
                now = self.clock()
                config = await self.get_dynamic_config("persona_config") or {}
                config.update({field["key"]: field["next"] for field in preview["fields"]})
                config["bot_qq"] = bot_qq
                await self._db.execute(
                    "INSERT INTO runtime_dynamic_configs VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                    ("persona_config", json.dumps(config, ensure_ascii=False), now),
                )
                await self._db.executemany("UPDATE voice_exemplars SET enabled=0 WHERE id=?",
                                          [(mid,) for mid in preview["disable_example_ids"]])
                await self._db.executemany(
                    "INSERT INTO voice_exemplars(id,scene_id,context,content,tag,created_at,source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    [(EXAMPLE_IDS[i], "", context, content, "嘉然", now, "operator")
                     for i, (context, content) in enumerate(EXAMPLES)],
                )
                await self._db.execute("INSERT INTO runtime_dynamic_configs VALUES(?,?,?)",
                                       (PRESET_ID, '{"applied":true}', now))
                await self._db.commit()
                return True
            except BaseException:
                await self._db.rollback()
                raise

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
        now = self.clock()
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
            "source": "operator",
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
                "SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at, source FROM voice_exemplars ORDER BY created_at DESC;"
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at, source
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
                "source": r[9],
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

    async def update_voice_example(self, example_id: str, *, scene_id: str, content: str, context: str, tag: str) -> bool:
        async with self._write_lock:
            cursor = await self._db.execute(
                "UPDATE voice_exemplars SET scene_id=?,content=?,context=?,tag=?,source='operator' WHERE id=?",
                (scene_id, content, context, tag, example_id),
            )
            await self._db.commit()
        return cursor.rowcount == 1

    async def select_voice_examples(self, scene_id: str) -> list[dict[str, Any]]:
        """All enabled operator examples, stable order; reading never changes selection."""
        cursor = await self._db.execute(
            """SELECT id,scene_id,context,content,tag,source FROM voice_exemplars
               WHERE enabled=1 AND scene_id IN (?, '') ORDER BY scene_id,id""", (scene_id,),
        )
        return [dict(zip(("id", "scene_id", "context", "content", "tag", "source"), row))
                for row in await cursor.fetchall()]

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
        deliveries: dict[str, str] | None = None,
        acknowledgements: dict[str, str] | None = None,
        scene_commit: dict | None = None,
        job_proposals=None,
        job_messages=None,
        origin_mode="live",
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

        now = self.clock()
        committed_tasks: list[TaskItem] = []
        resolved_loop_ids: list[str] = []
        committed_memories: list[MemoryItem] = []

        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                if scene_commit is not None:
                    perception = scene_commit["event"].payload["result"]["perception"]
                    references = {mid for update in [*perception.get("person_updates", []),
                                                     *perception.get("relationship_updates", [])]
                                  for mid in update.get("memory_ids_add", [])}
                    if references and not await self.memory_references_readable(references, scene_id):
                        raise ValueError("Working memory references changed or are outside readable scope")
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
                job_tasks, proposal_tasks = await self.apply_job_proposals_in_transaction(job_proposals or [], scene_id, episode_id, origin_mode)
                committed_tasks.extend(job_tasks)
                for tp in task_proposals:
                    if tp.payload.get("kind") == "agent_job":
                        raise ValueError("Use typed job_proposals for information work")
                    if tp.operation not in {"create", "update", "cancel", "result", "fail"}:
                        raise ValueError("Unknown task operation")
                    if tp.source_event_ids:
                        placeholders = ",".join("?" for _ in set(tp.source_event_ids))
                        evidence = await self._db.execute(
                            f"SELECT count(*) FROM events WHERE scene_id=? AND id IN ({placeholders})",
                            [scene_id, *set(tp.source_event_ids)],
                        )
                        if (await evidence.fetchone())[0] != len(set(tp.source_event_ids)):
                            raise ValueError("Task evidence outside scene")
                    if tp.operation != "create":
                        if tp.proposal_id:
                            proposal_tasks[tp.proposal_id] = tp.task_id
                        row = await self._db.execute(
                            "SELECT status,payload,due_at FROM tasks WHERE id=? AND scene_id=?",
                            (tp.task_id, scene_id),
                        )
                        existing = await row.fetchone()
                        if existing is None:
                            raise ValueError("Task not found in this scene")
                        status, payload, due_at = existing[0], json.loads(existing[1]), existing[2]
                        if payload.get("kind") == "agent_job":
                            raise ValueError("Use versioned job controls for information work")
                        if status not in {"pending", "claimed", "processing", "review_required", "result_ready"}:
                            raise ValueError("Task no longer editable")
                        if tp.operation == "update":
                            if tp.due_at is None or tp.due_at < now:
                                raise ValueError("Task update requires absolute due_at")
                            status, due_at = "pending", tp.due_at
                            payload.pop("result", None)
                        elif tp.operation == "cancel":
                            status = "cancelled"
                        elif tp.operation == "fail":
                            status = "failed"
                            payload["error"] = tp.result or tp.description
                        else:
                            if not tp.result:
                                raise ValueError("Task result cannot be empty")
                            status = "result_ready"
                            payload["result"] = tp.result
                        await self._db.execute(
                            "UPDATE tasks SET status=?,due_at=?,description=CASE WHEN ?='' THEN description ELSE ? END,payload=?,"
                            "origin_mode=CASE WHEN ?='shadow' THEN 'shadow' ELSE origin_mode END WHERE id=? AND scene_id=?",
                            (status, due_at, tp.description, tp.description, json.dumps(payload, ensure_ascii=False), tp.origin_mode, tp.task_id, scene_id),
                        )
                        continue
                    if not tp.description.strip():
                        raise ValueError("Task description is empty")
                    if tp.due_at is not None and tp.due_at < now:
                        raise ValueError("Task time is already past; review the promise instead")
                    tp.payload = {**tp.payload, "requester_id": tp.requester_id,
                                  "target_actor_id": tp.target_actor_id,
                                  "source_event_ids": tp.source_event_ids,
                                  "proposal_id": tp.proposal_id}
                    # A stable source + proposal id makes retries of the same proposal idempotent.
                    if tp.proposal_id:
                        prior = await self._db.execute(
                            """SELECT id,status FROM tasks WHERE scene_id=?
                               AND json_extract(payload,'$.proposal_id')=?
                               AND json_extract(payload,'$.source_event_ids')=?""",
                            (scene_id, tp.proposal_id, json.dumps(tp.source_event_ids, separators=(",", ":"))),
                        )
                        previous = await prior.fetchone()
                        if previous:
                            if previous[1] != "pending":
                                raise ValueError("This request already has a non-pending task; inspect it instead of confirming creation")
                            proposal_tasks[tp.proposal_id] = previous[0]
                            continue
                    task_id = f"task_{uuid.uuid4().hex[:10]}"
                    if tp.proposal_id:
                        proposal_tasks[tp.proposal_id] = task_id
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
                    due_at = tp.due_at if tp.due_at is not None else now + delay
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
                            task_id, scene_id, tp.description, due_at, episode_id,
                            payload_json, now, tp.wake_event_type, wake_match_json,
                            episode_id, getattr(tp, "origin_stimulus_id", None),
                            getattr(tp, "origin_mode", "live")
                        )
                    )
                    task_item = TaskItem(
                        id=task_id,
                        scene_id=scene_id,
                        description=tp.description,
                        due_at=due_at,
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

                for reference, action_id in (acknowledgements or {}).items():
                    await self._db.execute(
                        "UPDATE tasks SET payload=json_set(payload,'$.ack_action_id',?) WHERE id=? AND scene_id=?",
                        (action_id, proposal_tasks[reference], scene_id))

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

                for message in job_messages or []:
                    for segment in message.segments:
                        if segment.type == "image" and await self.get_media(segment.asset_id, [scene_id, "global-safe"]) is None:
                            raise ValueError("Message image is disabled or outside the scene")
                    if message.job_id:
                        await self.validate_job_message(scene_id, message.job_id, message.job_revision, bool(message.fulfils_task_id))
                    elif message.fulfils_task_id and await self.get_job(message.fulfils_task_id, scene_id):
                        raise ValueError("Job delivery requires job_id and job_revision")
                for task_id, action_id in (deliveries or {}).items():
                    cursor = await self._db.execute(
                        """UPDATE tasks SET status='awaiting_delivery',
                           payload=json_set(payload,'$.delivery_action_id',?)
                           WHERE id=? AND scene_id=? AND status IN ('processing','result_ready')
                           AND (COALESCE(json_extract(payload,'$.kind'),'reminder') != 'query'
                                OR json_extract(payload,'$.result') IS NOT NULL)""",
                        (action_id, task_id, scene_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("Task is not ready for fulfilment")

                if scene_commit is not None:
                    from len_bot.cognition.session import GroupAgentSession, GroupAgentSessionReducer
                    session = GroupAgentSession.model_validate(scene_commit["group_session_data"])
                    receipts = [await self._memory_receipt(mp, item)
                                for mp, item in zip(memory_proposals, committed_memories)]
                    GroupAgentSessionReducer.apply_memory_receipts(session, receipts)
                    scene_commit["group_session_data"] = session.model_dump()
                    scene_commit["event"].payload["memory_receipts"] = receipts
                    await self._write_scene_event(**scene_commit)

                # 5. Commit all mutations atomically in one transaction!
                await self._db.commit()
                return committed_tasks, resolved_loop_ids, committed_memories
            except BaseException:
                await self._db.rollback()
                raise

    async def _memory_receipt(self, proposal: MemoryProposal, item: MemoryItem) -> dict:
        replaced = await (await self._db.execute(
            """WITH RECURSIVE replaced(id) AS (
                   SELECT id FROM memories WHERE scope=? AND superseded_by=?
                   UNION SELECT m.id FROM memories m JOIN replaced r ON m.superseded_by=r.id WHERE m.scope=?
               ) SELECT id FROM replaced""", (item.scope, item.id, item.scope),
        )).fetchall()
        return {"id": item.id, "subject": item.subject, "kind": item.kind.value, "key": item.key,
                "value": item.value, "status": item.status.value, "operation": proposal.operation,
                "reason": proposal.reason, "evidence": proposal.evidence,
                "target_memory_ids": list(dict.fromkeys(proposal.target_memory_ids + [r[0] for r in replaced]))}

    async def memory_references_readable(self, memory_ids: set[str], scene_id: str) -> bool:
        cursor = await self._db.execute(
            f"SELECT COUNT(*) FROM memories WHERE scope IN (?, 'global-safe') AND status='active' AND id IN ({','.join('?' for _ in memory_ids)})",
            [scene_id, *memory_ids],
        )
        return (await cursor.fetchone())[0] == len(memory_ids)

    async def scene_tasks(self, scene_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT id,description,due_at,status,payload,origin_mode FROM tasks WHERE scene_id=? ORDER BY created_at",
            (scene_id,),
        )
        return [dict(id=r[0], description=r[1], due_at=r[2], status=r[3],
                     payload=json.loads(r[4]), origin_mode=r[5]) for r in await cursor.fetchall()]

    async def pending_runtime_events(self) -> list[Event]:
        cursor = await self._db.execute("SELECT event_json FROM pending_runtime_events ORDER BY rowid")
        return [Event.model_validate_json(row[0]) for row in await cursor.fetchall()]

    async def event_exists(self, event_id: str, scene_id: str) -> bool:
        cursor = await self._db.execute("SELECT 1 FROM events WHERE id=? AND scene_id=?", (event_id, scene_id))
        return await cursor.fetchone() is not None

    async def task_due_is_current(self, event: Event) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM tasks WHERE id=? AND scene_id=? AND status='claimed' AND trigger_event_id=?",
            (event.payload['task_id'], event.scene_id, event.payload.get('trigger_event_id') or event.id))
        return await cursor.fetchone() is not None

    async def recover_social_work(self) -> list[str]:
        """Persist a review wake for an interrupted turn; never replay old sends."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "SELECT scene_id,last_observed_event_rowid,last_cognized_event_rowid FROM group_agent_sessions"
                )
                rows = await cursor.fetchall()
                for scene_id, observed, cognized in rows:
                    unseen = await self.get_events_since(scene_id, cognized, limit=12000,
                        event_types=[EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
                                     EventType.TASK_DUE, EventType.TASK_REVIEW, EventType.REFLECTION_RECORDED,
                                     EventType.LIVE_STARTED, EventType.LIVE_ENDED])
                    unseen = [e for e in unseen if e.metadata['_rowid'] <= observed
                              and (e.event_type != EventType.REFLECTION_RECORDED or e.metadata.get('needs_review'))]
                    if not unseen:
                        continue
                    pending = await self._db.execute("SELECT 1 FROM pending_runtime_events WHERE scene_id=?", (scene_id,))
                    if await pending.fetchone():
                        continue
                    event = Event(id=f"resume:{scene_id}:{observed}", event_type=EventType.TASK_REVIEW,
                        scene_id=scene_id, actor_id="system:recovery", timestamp=self.clock(),
                        payload={"source_event_ids": [e.id for e in unseen],
                                 "raw_text": "重启前有输入尚未完成认知。结合当前时间、现有任务和最近原话核对；过期请求不要自动补发或重设相对时间。"})
                    await self._db.execute("INSERT INTO pending_runtime_events VALUES (?,?,?)",
                        (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
                return [r[0] for r in rows]
            except BaseException:
                await self._db.rollback()
                raise

    async def claim_task_event(self, task_id: str, scene_id: str, event: Event) -> bool:
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "UPDATE tasks SET status='claimed',trigger_event_id=? WHERE id=? AND scene_id=? AND status='pending'",
                    (event.payload.get("trigger_event_id") or event.id, task_id, scene_id),
                )
                if cursor.rowcount != 1:
                    await self._db.rollback()
                    return False
                await self._db.execute("INSERT INTO pending_runtime_events VALUES (?,?,?)",
                                       (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
                return True
            except BaseException:
                await self._db.rollback()
                raise

    async def migrate_social_continuity(self) -> None:
        """One-time audit of old reflected ranges; only cognition may act on reviews."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "SELECT 1 FROM runtime_dynamic_configs WHERE key='adr0040_migration'")
                if await cursor.fetchone():
                    return
                await self._db.execute("UPDATE reflection_cursors SET last_event_rowid=0")
                await self._db.execute(
                    "INSERT INTO runtime_dynamic_configs(key,value_json,updated_at) VALUES ('adr0040_migration',?,?)",
                    ('{"version":1}', self.clock()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def recover_task_execution(self) -> None:
        async with self._write_lock:
            try:
                await self._db.execute("UPDATE tasks SET status='delivery_unknown' WHERE status='awaiting_delivery'")
                cursor = await self._db.execute(
                    "SELECT id,scene_id,description,status,origin_mode FROM tasks WHERE status IN ('processing','claimed','result_ready','review_required')"
                )
                for task_id, scene_id, description, status, origin_mode in await cursor.fetchall():
                    pending = await self._db.execute(
                        "SELECT 1 FROM pending_runtime_events WHERE json_extract(event_json,'$.payload.task_id')=?",
                        (task_id,),
                    )
                    if await pending.fetchone():
                        continue
                    event = Event(event_type=EventType.TASK_REVIEW, scene_id=scene_id,
                                  actor_id="system:recovery", payload={
                                      "task_id": task_id, "origin_mode": origin_mode,
                                      "raw_text": f"重启后待核对任务：{description}。检查当前时间和结果，不要假定已完成。",
                                  })
                    await self._db.execute("UPDATE tasks SET status='review_required' WHERE id=?", (task_id,))
                    await self._db.execute("INSERT INTO pending_runtime_events VALUES (?,?,?)",
                                           (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
