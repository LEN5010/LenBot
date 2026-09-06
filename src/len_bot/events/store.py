import asyncio
import aiosqlite
import json
import logging
import time
import uuid
from typing import Any, Optional
from len_bot.events.models import Event, EventType
from len_bot.memory.writes import validate_memory_proposal, commit_memory_proposal_core
from len_bot.memory.models import MemoryProposal, MemoryItem
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
        retired = await (await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('group_agent_sessions','scene_states','episodes')"
        )).fetchall()
        if retired:
            await self.close()
            raise RuntimeError("旧会话结构需要先停机备份并执行获准的 VNext Reset")
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
        await self._db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
            event_id UNINDEXED, scene_id UNINDEXED, actor_id UNINDEXED, content,
            tokenize='trigram')""")

        await self._db.execute("""CREATE TABLE IF NOT EXISTS scene_sessions (
            scene_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
            last_observed_event_rowid INTEGER NOT NULL, last_cognized_event_rowid INTEGER NOT NULL,
            state_json TEXT NOT NULL, updated_at REAL NOT NULL)""")
        await self._db.execute("""CREATE TABLE IF NOT EXISTS reflection_cursors (
            scene_id TEXT PRIMARY KEY, last_event_rowid INTEGER NOT NULL, updated_at REAL NOT NULL)""")

        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS open_loops (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL,
                target_actor_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                source_stimulus_id TEXT
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
                created_at REAL NOT NULL,
                segments_json TEXT NOT NULL DEFAULT '[]'
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_voice_exemplars_scene ON voice_exemplars(scene_id, enabled);")
        voice_columns = await (await self._db.execute("PRAGMA table_info(voice_exemplars)")).fetchall()
        if "source" not in {row[1] for row in voice_columns}:
            await self._db.execute("ALTER TABLE voice_exemplars ADD COLUMN source TEXT NOT NULL DEFAULT 'operator'")
        if "segments_json" not in {row[1] for row in voice_columns}:
            await self._db.execute("ALTER TABLE voice_exemplars ADD COLUMN segments_json TEXT NOT NULL DEFAULT '[]'")
            await self._db.execute("""UPDATE voice_exemplars
                SET segments_json=json_array(json_object('type','text','text',content)) WHERE content!=''""")

        await self._db.commit()

        await self._db.execute(
            "INSERT INTO runtime_dynamic_configs VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            ("delivery_scenes", '{"scene_ids":["group:126300994"]}', self.clock()))
        await self._db.commit()

    async def reset_conversation_data(self, event: Event) -> dict[str, int]:
        """Explicit operator reset; configuration and authored voice stay intact."""
        tables = ("events_fts", "pending_runtime_events", "agent_jobs", "tool_observations",
                  "media_assets", "traces", "reflection_cursors", "memories",
                  "open_loops", "tasks", "scene_sessions", "events")
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
        c4 = await self._db.execute("SELECT COUNT(*) FROM scene_sessions;")
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

    async def project_reply_context(self, scene_id: str, events: list[Event], through_rowid=None) -> list[Event]:
        """Attach read-only, scene-scoped quote facts to copies, never raw history."""
        refs = {str(e.payload["reply_to_message_id"]) for e in events if e.payload.get("reply_to_message_id") is not None}
        if not refs:
            return events
        cursor = await self._db.execute(
            "SELECT id, actor_id, payload,metadata,rowid FROM events WHERE scene_id=? AND (? IS NULL OR rowid<=?) AND "
            "CAST(json_extract(payload, '$.message_id') AS TEXT) IN (" + ",".join("?" for _ in refs) + ")",
            (scene_id, through_rowid, through_rowid, *sorted(refs)),
        )
        quotes = {}
        for event_id, actor_id, payload_json, metadata_json, rowid in await cursor.fetchall():
            payload = json.loads(payload_json)
            quotes[str(payload["message_id"])] = {
                "event_id": event_id, "actor_id": actor_id, "rowid": rowid,
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

    async def outbound_message_facts(
        self, scene_id: str, through_rowid: int, *, bot_actor_id: str, limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Read approved expressions and their latest same-scene receipt atomically.

        Conversation commits precede queue delivery. They already authorize an
        expression, but are not MESSAGE_SENT. Receipts beyond the conversation's
        input cutoff remain outbound status facts, without consuming that input.
        Actor commit IDs are ``turn:{episode_id}``; Queue uses that episode ID as
        batch_id and the message's array position as batch_index.
        """
        if not scene_id or not bot_actor_id or through_rowid < 0:
            raise ValueError("Outbound facts require a scene, Bot identity and read cutoff")
        rows = await (await self._db.execute(
            """WITH approved AS (
                SELECT rowid AS approval_rowid,id,timestamp,payload,metadata,substr(id,6) AS batch_id
                FROM events WHERE scene_id=? AND event_type='CONVERSATION_COMMITTED'
                  AND id LIKE 'turn:%' AND json_array_length(payload,'$.outcome.message_proposals')>0
                ORDER BY rowid DESC LIMIT ?
            )
            SELECT a.id,a.timestamp,a.batch_id,a.metadata,m.key,m.value,
                   r.rowid,r.id,r.event_type,r.timestamp,r.payload,r.metadata
            FROM approved a JOIN json_each(a.payload,'$.outcome.message_proposals') m
            LEFT JOIN events r ON r.rowid=(
                SELECT receipt.rowid FROM events receipt
                WHERE receipt.scene_id=? AND receipt.actor_id=?
                  AND receipt.event_type IN ('MESSAGE_SENT','MESSAGE_SEND_FAILED','ACTION_SHADOWED')
                  AND json_extract(receipt.payload,'$.batch_id')=a.batch_id
                  AND COALESCE(json_extract(receipt.payload,'$.batch_index'),0)=CAST(m.key AS INTEGER)
                ORDER BY receipt.rowid DESC LIMIT 1
            )
            WHERE r.rowid IS NULL OR r.event_type!='MESSAGE_SENT' OR r.rowid>?
            ORDER BY a.approval_rowid,CAST(m.key AS INTEGER)""",
            (scene_id, max(1, min(limit, 20)), scene_id, bot_actor_id, through_rowid),
        )).fetchall()
        facts = []
        for approval_id, approved_at, batch_id, approval_metadata, index, message_json, receipt_rowid, receipt_id, kind, receipt_at, receipt_json, receipt_metadata in rows:
            message = json.loads(message_json)
            receipt = json.loads(receipt_json) if receipt_json else {}
            simulated = bool(json.loads(receipt_metadata).get("simulated")) if receipt_metadata else False
            if receipt_id is None:
                status = "pending"
            elif kind == "ACTION_SHADOWED":
                status = "shadow"
            elif kind == "MESSAGE_SENT":
                status = "simulated_sent" if simulated else "sent"
            elif receipt.get("delivery_unknown") or receipt.get("delivery_status") == "unknown":
                status = "unknown"
            else:
                status = receipt.get("delivery_status")
                if status not in {"not_sent", "rejected"}:
                    status = "unknown"
            facts.append({
                "approval_event_id": approval_id, "approved_at": approved_at,
                "batch_id": batch_id, "batch_index": int(index),
                "approval_mode": json.loads(approval_metadata).get("mode"),
                "segments": receipt.get("segments", message.get("segments", [])), "status": status,
                "body_source": "receipt" if "segments" in receipt else "approval",
                "receipt_event_id": receipt_id, "receipt_at": receipt_at,
                "receipt_after_cutoff": receipt_rowid is not None and receipt_rowid > through_rowid,
                "simulated": simulated,
            })
        return facts

    async def own_sent_message_ids(self, actor_id: str, limit: int = 1000) -> list[str]:
        cursor = await self._db.execute(
            "SELECT json_extract(payload, '$.message_id') FROM events "
            "WHERE event_type = ? AND actor_id = ? AND json_extract(payload, '$.message_id') IS NOT NULL "
            "ORDER BY rowid DESC LIMIT ?", (EventType.MESSAGE_SENT.value, actor_id, limit),
        )
        return [str(row[0]) for row in reversed(await cursor.fetchall())]

    async def get_recent_events(self, scene_id, limit=50, through_rowid=None):
        cursor=await self._db.execute("""
            SELECT rowid,id,event_type,scene_id,actor_id,timestamp,payload,metadata FROM (
                SELECT rowid,* FROM events WHERE scene_id=? AND (? IS NULL OR rowid<=?)
                ORDER BY rowid DESC LIMIT ?) ORDER BY rowid""",
            (scene_id,through_rowid,through_rowid,limit))
        return [Event(id=r[1],event_type=r[2],scene_id=r[3],actor_id=r[4],timestamp=r[5],
            payload=json.loads(r[6]),metadata={**json.loads(r[7]),'_rowid':r[0]}) for r in await cursor.fetchall()]

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
        self, scene_id, proposals, source_event_ids, new_cursor_rowid, review_event,
        expected_cursor_rowid, expected_revision, scene_state_data, *, bot_actor_id,
    ):
        """Actor-owned reflection: beliefs, receipt, factual session and cursor commit together."""
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                current = await (await self._db.execute(
                    "SELECT json_extract(state_json,'$.knowledge_revision') FROM scene_sessions WHERE scene_id=?",
                    (scene_id,))).fetchone()
                if (current[0] if current else 0) != expected_revision:
                    raise ReflectionConflictError("认识版本已变化")
                cursor = await (await self._db.execute(
                    "SELECT last_event_rowid FROM reflection_cursors WHERE scene_id=?", (scene_id,))).fetchone()
                if (cursor[0] if cursor else 0) != expected_cursor_rowid:
                    raise ReflectionConflictError("反思读取截点已变化")
                if not source_event_ids or not await self.references_belong_to_scene(set(source_event_ids), scene_id, new_cursor_rowid):
                    raise ValueError("Reflection needs readable original sources")
                last = await (await self._db.execute(
                    "SELECT max(rowid) FROM events WHERE scene_id=? AND id IN (SELECT value FROM json_each(?))",
                    (scene_id, json.dumps(source_event_ids)))).fetchone()
                if last[0] != new_cursor_rowid:
                    raise ValueError("Reflection cutoff must match its last source")
                if review_event.scene_id != scene_id or review_event.event_type != EventType.REFLECTION_RECORDED:
                    raise ValueError("Invalid reflection receipt")
                for item in review_event.payload.get("review_items", []):
                    refs = item.get("source_event_ids", [])
                    if not refs or not set(refs).issubset(source_event_ids):
                        raise ValueError("Review item needs reflected sources")
                committed = []
                for proposal in proposals:
                    await validate_memory_proposal(self._db, proposal, scene_id, new_cursor_rowid, bot_actor_id=bot_actor_id)
                    committed.append(await commit_memory_proposal_core(
                        self._db, proposal, scene_id, now=self.clock(), revision_event_id=review_event.id))
                review_event.payload["memory_receipts"] = [
                    await self._memory_receipt(proposal, item) for proposal, item in zip(proposals, committed)]
                rowid = await self._write_scene_event(review_event, scene_state_data,
                    advance_session_observation=bool(review_event.payload.get("review_items")))
                await self._db.execute(
                    "INSERT INTO reflection_cursors VALUES(?,?,?) ON CONFLICT(scene_id) DO UPDATE SET last_event_rowid=excluded.last_event_rowid,updated_at=excluded.updated_at",
                    (scene_id, new_cursor_rowid, self.clock()))
                await self._db.commit()
                return committed, rowid
            except BaseException:
                await self._db.rollback()
                raise

    @staticmethod
    def _retrieval_event(row):
        return {"id": row[0], "event_type": row[1], "scene_id": row[2], "actor_id": row[3],
                "timestamp": row[4], "payload": json.loads(row[5]), "metadata": {**json.loads(row[7]), "_rowid": row[6]}}

    async def search_messages(self, query, allowed_scopes, limit=20, through_rowid=None):
        if not allowed_scopes: return []
        placeholders = ",".join("?" for _ in allowed_scopes)
        # FTS input is literal text; quotes/operators from chat cannot alter the query.
        clause = "f.content MATCH ?" if len(query) >= 3 else "f.content LIKE ?"
        term = '"' + query.replace('"', '""') + '"' if len(query) >= 3 else f"%{query}%"
        cursor = await self._db.execute(f"""
            SELECT e.id,e.event_type,e.scene_id,e.actor_id,e.timestamp,e.payload,e.rowid,e.metadata
            FROM events_fts f JOIN events e ON f.event_id=e.id
            WHERE {clause} AND e.scene_id IN ({placeholders})
              AND (? IS NULL OR e.rowid<=?) ORDER BY e.rowid DESC LIMIT ?""",
            [term,*allowed_scopes,through_rowid,through_rowid,max(1,min(limit,50))])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def read_context(self, event_id, before=3, after=3, allowed_scopes=None, through_rowid=None):
        if not allowed_scopes: return []
        placeholders=",".join("?" for _ in allowed_scopes)
        target=await (await self._db.execute(f"""
            SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
            WHERE id=? AND scene_id IN ({placeholders}) AND (? IS NULL OR rowid<=?)""",
            [event_id,*allowed_scopes,through_rowid,through_rowid])).fetchone()
        if not target: return []
        rows=[]
        for operator,order,limit in (("<","DESC",before),(">","ASC",after)):
            cursor=await self._db.execute(f"""
                SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
                WHERE scene_id=? AND rowid {operator} ? AND (? IS NULL OR rowid<=?)
                ORDER BY rowid {order} LIMIT ?""",[target[2],target[6],through_rowid,through_rowid,max(0,min(limit,25))])
            rows.extend(await cursor.fetchall())
        return [self._retrieval_event(r) for r in sorted([*rows,target],key=lambda r:r[6])]

    async def references_belong_to_scene(self, reference_ids, scene_id, through_event_rowid=None):
        if not reference_ids: return True
        ids=list(reference_ids);placeholders=",".join("?" for _ in ids)
        cursor=await self._db.execute(f"""SELECT id FROM events WHERE scene_id=?
            AND id IN ({placeholders}) AND (? IS NULL OR rowid<=?)""",
            [scene_id,*ids,through_event_rowid,through_event_rowid])
        return {r[0] for r in await cursor.fetchall()} == set(reference_ids)

    async def query_timeline(self, scene_id, start_time, end_time, allowed_scopes=None, limit=20, through_rowid=None):
        if not allowed_scopes or scene_id not in allowed_scopes: return []
        cursor=await self._db.execute("""
            SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
            WHERE scene_id=? AND timestamp>=? AND timestamp<=? AND (? IS NULL OR rowid<=?)
            ORDER BY rowid LIMIT ?""",[scene_id,start_time,end_time,through_rowid,through_rowid,max(1,min(limit,50))])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def query_person_history(self, actor_id, allowed_scopes=None, limit=15, through_rowid=None):
        if not allowed_scopes: return []
        placeholders=",".join("?" for _ in allowed_scopes)
        cursor=await self._db.execute(f"""
            SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
            WHERE actor_id=? AND scene_id IN ({placeholders}) AND (? IS NULL OR rowid<=?)
            ORDER BY rowid DESC LIMIT ?""",[actor_id,*allowed_scopes,through_rowid,through_rowid,max(1,min(limit,50))])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def load_scene_session(self, scene_id):
        row = await (await self._db.execute(
            "SELECT state_json FROM scene_sessions WHERE scene_id=?", (scene_id,))).fetchone()
        return json.loads(row[0]) if row else None

    async def commit_scene_event(
        self,
        event: Event,
        scene_state_data: dict[str, Any],
        task_id_to_trigger: Optional[str] = None,
        associated_open_loop: Optional[dict[str, Any]] = None,
        advance_session_observation: bool = True,
    ) -> int:
        """Atomically commit an Event and its materialized scene state."""
        async with self._write_lock:
            try:
                rowid = await self._write_scene_event(
                    event, scene_state_data, task_id_to_trigger, associated_open_loop,
                    advance_session_observation,
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

        persisted = dict(scene_state_data)
        if advance_session_observation:
            persisted["last_observed_event_rowid"] = event_rowid
        await self._db.execute(
            "INSERT INTO scene_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(scene_id) DO UPDATE SET "
            "version=excluded.version,last_observed_event_rowid=excluded.last_observed_event_rowid,"
            "last_cognized_event_rowid=excluded.last_cognized_event_rowid,state_json=excluded.state_json,updated_at=excluded.updated_at",
            (event.scene_id, persisted.get("version", 0), persisted.get("last_observed_event_rowid", 0),
             persisted.get("last_cognized_event_rowid", 0), json.dumps(persisted, ensure_ascii=False), self.clock()))

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

    async def preview_diana_persona(self, media_refs: dict[str, str] | None = None) -> dict:
        import hashlib
        from len_bot.cognition.diana import PERSONA, PRESET_ID, PREVIOUS_PERSONAS, PREVIOUS_EXAMPLES, MEDIA_REF_TAGS, build_examples
        current = await self.get_dynamic_config("persona_config") or {}
        applied = await self.get_dynamic_config(PRESET_ID) is not None
        examples = await self.list_voice_examples()
        if media_refs is None:
            palette = await self.list_palette("global-safe")
            media_refs = {name: next((asset["id"] for asset in palette if tag in asset["tags"]), "")
                          for name, tag in MEDIA_REF_TAGS.items()}
        available_refs = {}
        for name, asset_id in media_refs.items():
            asset = await self.get_media(asset_id, ["global-safe"])
            if asset and asset["curated"]:
                available_refs[name] = asset_id
        desired_examples = build_examples(available_refs, strict=False)
        fields = []
        for key, desired in PERSONA.items():
            previous = current.get(key)
            update = not applied and (previous is None or any(previous == baseline[key] for baseline in PREVIOUS_PERSONAS))
            fields.append({"key": key, "current": previous, "next": desired if update else previous,
                           "action": "update" if update and previous != desired else
                                     "preserve" if previous != desired else "unchanged"})
        disable = [item["id"] for item in examples if item["id"] in PREVIOUS_EXAMPLES
                   and (item["context"], item["content"]) == PREVIOUS_EXAMPLES[item["id"]]
                   and not item["scene_id"] and item["tag"] in {"", "嘉然"}
                   and item["segments"] == [{"type": "text", "text": item["content"]}]]
        token = hashlib.sha256(json.dumps([current, examples, applied, desired_examples], sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()
        return {"preset_id": PRESET_ID, "applied": applied, "fields": fields,
                "disable_example_ids": disable,
                "example_count": sum(item["id"] not in {existing["id"] for existing in examples} for item in desired_examples),
                "examples": desired_examples, "preview_token": token,
                "missing_media": sorted({MEDIA_REF_TAGS[name] for item in desired_examples for name in item["missing_media_refs"]})}

    async def apply_diana_persona(self, bot_qq: int, expected_token: str | None = None, *, media_refs: dict[str, str] | None = None) -> bool:
        """Explicit, atomic preset migration. Preserve edits; never run on startup."""
        from len_bot.cognition.diana import PRESET_ID
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                preview = await self.preview_diana_persona(media_refs)
                if expected_token is not None and expected_token != preview["preview_token"]:
                    raise ValueError("配置已变化，请重新预览后应用")
                if preview["applied"]:
                    await self._db.rollback()
                    return False
                if preview["missing_media"]:
                    raise ValueError("请先将这些情绪的运营素材选入固定目录：" + "、".join(preview["missing_media"]))
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
                    "INSERT INTO voice_exemplars(id,scene_id,context,content,segments_json,tag,created_at,source) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                    [(item["id"], "", item["context"], item["content"], json.dumps(item["segments"], ensure_ascii=False), "嘉然", now, "operator")
                     for item in preview["examples"]],
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
        content: str = "",
        context: str = "",
        tag: str = "",
        segments: list | None = None,
    ) -> dict[str, Any]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        example_id = f"voice_{uuid.uuid4().hex[:10]}"
        now = self.clock()
        async with self._write_lock:
            content, segments = await self._voice_body(scene_id, content, segments)
            await self._db.execute(
                """
                INSERT INTO voice_exemplars (id, scene_id, context, content, segments_json, tag, enabled, use_count, last_used_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, ?);
                """,
                (example_id, scene_id or "", context, content, json.dumps(segments, ensure_ascii=False), tag, now),
            )
            await self._db.commit()
        return {
            "id": example_id,
            "scene_id": scene_id or "",
            "context": context,
            "content": content,
            "segments": segments,
            "tag": tag,
            "source": "operator",
            "enabled": True,
            "use_count": 0,
            "last_used_at": 0.0,
            "created_at": now,
        }

    async def _voice_body(self, scene_id: str, content: str, segments: list | None):
        from len_bot.media.models import MessageSegment, segment_text
        if scene_id and not scene_id.startswith(("group:", "private:")):
            raise ValueError("样例范围须留空或填写 group:/private: 场景")
        parts = [MessageSegment.model_validate(item) for item in segments] if segments is not None else [MessageSegment(type="text", text=content)]
        if not parts or not segment_text(parts).strip():
            raise ValueError("样例需要文字或图片")
        scopes = [scene_id, "global-safe"] if scene_id else ["global-safe"]
        for part in parts:
            if part.type == "image":
                asset = await self.get_media(part.asset_id, scopes)
                if not asset or not asset["curated"]:
                    raise ValueError("样例图片须是启用且在样例范围内的运营素材")
        return segment_text(parts), [part.model_dump(exclude_none=True) for part in parts]

    @staticmethod
    def _voice_row(row):
        item = dict(zip(("id", "scene_id", "context", "content", "tag", "enabled", "use_count", "last_used_at", "created_at", "source", "segments"), row))
        item["enabled"] = bool(item["enabled"])
        item["segments"] = json.loads(item["segments"])
        return item

    async def list_voice_examples(self, scene_id: str | None = None) -> list[dict[str, Any]]:
        """All exemplars; when scene_id is given, that scene's plus global ones."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        if scene_id is None:
            cursor = await self._db.execute(
                "SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at, source, segments_json FROM voice_exemplars ORDER BY created_at DESC,id;"
            )
        else:
            cursor = await self._db.execute(
                """
                SELECT id, scene_id, context, content, tag, enabled, use_count, last_used_at, created_at, source, segments_json
                FROM voice_exemplars
                WHERE scene_id IN (?, '')
                ORDER BY created_at DESC,id;
                """,
                (scene_id,),
            )
        rows = await cursor.fetchall()
        examples = [self._voice_row(row) for row in rows]
        for item in examples:
            try:
                await self._voice_body(item["scene_id"], item["content"], item["segments"])
            except ValueError as error:
                item.update(available=False, unavailable_reason=str(error))
            else:
                item["available"] = True
        return examples

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
            if enabled:
                row = await (await self._db.execute("SELECT scene_id,content,segments_json FROM voice_exemplars WHERE id=?", (example_id,))).fetchone()
                if row is None:
                    return False
                await self._voice_body(row[0], row[1], json.loads(row[2]))
            cursor = await self._db.execute(
                "UPDATE voice_exemplars SET enabled = ? WHERE id = ?;",
                (1 if enabled else 0, example_id),
            )
            await self._db.commit()
        return cursor.rowcount > 0

    async def update_voice_example(self, example_id: str, *, scene_id: str, content: str = "", context: str, tag: str, segments: list | None = None) -> bool:
        async with self._write_lock:
            content, segments = await self._voice_body(scene_id, content, segments)
            cursor = await self._db.execute(
                "UPDATE voice_exemplars SET scene_id=?,content=?,segments_json=?,context=?,tag=?,source='operator' WHERE id=?",
                (scene_id, content, json.dumps(segments, ensure_ascii=False), context, tag, example_id),
            )
            await self._db.commit()
        return cursor.rowcount == 1

    async def select_voice_examples(self, scene_id: str) -> list[dict[str, Any]]:
        """All enabled operator examples, stable order; reading never changes selection."""
        examples = await self.list_voice_examples(scene_id)
        return sorted((item for item in examples if item["enabled"] and item["available"]),
                      key=lambda item: (item["scene_id"], item["id"]))

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
        bot_actor_id: str = "",
        through_rowid: int | None = None,
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
        from len_bot.memory.models import MemoryItem
        from len_bot.cognition.models import CONDITION_TASK_DEFAULT_DEADLINE_SECONDS

        now = self.clock()
        committed_tasks: list[TaskItem] = []
        resolved_loop_ids: list[str] = []
        committed_memories: list[MemoryItem] = []

        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                for mp in memory_proposals:
                    await validate_memory_proposal(self._db, mp, scene_id, through_rowid, bot_actor_id=bot_actor_id)

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
                    await validate_memory_proposal(self._db, mp, scene_id, through_rowid, bot_actor_id=bot_actor_id)
                    mem_item = await commit_memory_proposal_core(self._db, mp, scene_id, now=now,
                        revision_event_id=scene_commit["event"].id if scene_commit else None)
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
                    scene_commit["event"].payload["memory_receipts"] = [
                        await self._memory_receipt(mp, item) for mp, item in zip(memory_proposals, committed_memories)]
                    await self._write_scene_event(**scene_commit)

                # 5. Commit all mutations atomically in one transaction!
                await self._db.commit()
                return committed_tasks, resolved_loop_ids, committed_memories
            except BaseException:
                await self._db.rollback()
                raise

    async def _memory_receipt(self, proposal, item):
        return {"id": item.id, "subject": item.subject, "kind": item.kind,
                "statement": item.statement, "basis": item.basis, "status": item.status,
                "operation": proposal.operation, "reason": proposal.reason,
                "evidence": proposal.evidence, "target_memory_ids": proposal.target_memory_ids}

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
                    "SELECT scene_id,last_observed_event_rowid,last_cognized_event_rowid FROM scene_sessions"
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
