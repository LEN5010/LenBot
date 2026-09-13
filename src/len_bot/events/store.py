import asyncio
import aiosqlite
import json
import logging
import time
import uuid
import re
from typing import Any, Optional
from len_bot.events.models import Event, EventType, PluginOrigin
from len_bot.memory.writes import validate_memory_proposal, commit_memory_proposal_core
from len_bot.memory.models import MemoryProposal, MemoryItem
from len_bot.tools.observations import ObservationStoreMixin
from len_bot.runtime.job_store import JobStoreMixin
from len_bot.media.store import MediaStoreMixin
from len_bot.cognition.call_store import ModelCallStoreMixin
from len_bot.cognition.models import EpisodeOutcome, MessageProposal, OperationReceipt
from len_bot.memory.history import HistoryStoreMixin
from len_bot.scheduler.models import TaskItem, TaskStatus

logger = logging.getLogger(__name__)

class EventStore(ObservationStoreMixin, JobStoreMixin, MediaStoreMixin, ModelCallStoreMixin, HistoryStoreMixin):
    def __init__(self, db_path: str = "len_bot.db", clock=time.time):
        self.clock = clock
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()
        self.resolve_plugin_work = None
        # The Runtime sets these three once.  They are the live configuration
        # a reservation is computed from (its step/context/output limits), the
        # capability authority whose grant names the quota policy, and the
        # business timezone the billing day is counted on.  Absent (a store
        # built without a Runtime) still reserves, using the project's own
        # default policy, so a work is never silently unlimited.
        self.budget_config = None
        self.capability_authority = None
        self.billing_timezone = None

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        retired = await (await self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('group_agent_sessions','scene_states','episodes')"
        )).fetchall()
        if retired:
            await self.close()
            raise RuntimeError("非现行会话结构；保持停机，使用对应旧版本完成离线处理后再启动")
        columns = await (await self._db.execute('PRAGMA table_info(scene_sessions)')).fetchall()
        if any(column[1] == 'last_cognized_event_rowid' for column in columns):
            await self.close()
            raise RuntimeError("非现行注意力结构；保持停机，使用对应旧版本完成离线处理后再启动")
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self.initialize_observations()
        await self.initialize_jobs()
        await self.initialize_media()
        await self.initialize_model_calls()
        await self.initialize_history()
        
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
            last_observed_event_rowid INTEGER NOT NULL, attention_scanned_event_rowid INTEGER NOT NULL,
            state_json TEXT NOT NULL, updated_at REAL NOT NULL)""")

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
                segments_json TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT 'operator'
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_voice_exemplars_scene ON voice_exemplars(scene_id, enabled);")

        await self._db.commit()

    async def reset_conversation_data(self, event: Event) -> dict[str, int]:
        """Explicit operator reset; configuration and authored voice stay intact."""
        tables = ("events_fts", "pending_runtime_events", "job_exchanges", "skill_candidates",
                  "skill_versions", "skills", "agent_jobs", "tool_observations",
                  "media_assets", "traces", "history_batches", "history_origins", "model_calls", "memories", "memory_index",
                  "open_loops", "tasks", "scene_sessions", "events")
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                counts = {}
                for table in tables:
                    where = " WHERE skill_id IN (SELECT id FROM skills WHERE author='agent')" if table == 'skill_versions' else (
                        " WHERE author='agent'" if table == 'skills' else (
                        " WHERE curated=0" if table == "media_assets" else (
                        " WHERE id NOT IN (SELECT source_event_id FROM media_assets) AND "
                        "NOT (event_type='MEDIA_UPDATED' AND COALESCE(json_extract(payload,'$.asset_id') "
                        "IN (SELECT id FROM media_assets),0))" if table == "events" else "")))
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
                "interaction": json.loads(metadata_json).get("interaction"),
                "plugin_origin": payload.get('plugin_origin'),
                "plugin_routes": json.loads(metadata_json).get('plugin_routes', []),
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
        self, scene_id: str, through_rowid: int, *, bot_actor_id: str, limit: int,
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
        if type(limit) is not int or limit < 1:
            raise ValueError("Outbound facts require a positive integer limit")
        rows = await (await self._db.execute(
            """WITH approved AS (
                SELECT rowid AS approval_rowid,id,timestamp,payload,metadata,substr(id,6) AS batch_id
                FROM events WHERE scene_id=? AND event_type='CONVERSATION_COMMITTED'
                  AND COALESCE(json_extract(payload,'$.output_kind'),'chat')='chat'
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
            (scene_id, limit, scene_id, bot_actor_id, through_rowid),
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

    async def uncommitted_job_attempts(self, scene_id: str, after_rowid: int, through_rowid: int) -> list[dict[str, Any]]:
        """Prior rejected intent is a failure record, never an actual job.

        Only show it while the input that it read remains unconsumed. A trace
        recorded after an accepted commit must never become a rejected intent.
        """
        rows = await (await self._db.execute(
            """SELECT ref_id,payload FROM traces t
               WHERE scene_id=? AND kind='conversation_error'
                 AND json_extract(payload,'$.error_type') IN
                     ('SceneCommitConflict','FreshInputConflict','CommitConflict','AgentProtocolError','AgentBudgetExhausted','TruncatedModelOutput')
                 AND COALESCE(json_extract(payload,'$.gate.accepted'),0)=0
                 AND COALESCE(json_extract(payload,'$.gate.committed'),0)=0
                 AND NOT EXISTS (SELECT 1 FROM events e WHERE e.scene_id=t.scene_id
                     AND (e.id='turn:'||t.ref_id OR json_extract(e.payload,'$.episode_id')=t.ref_id)
                     AND e.event_type='CONVERSATION_COMMITTED')
                 AND COALESCE(json_extract(payload,'$.conversation.read_cutoff'),json_extract(payload,'$.observed_rowid'))>?
                 AND COALESCE(json_extract(payload,'$.conversation.read_cutoff'),json_extract(payload,'$.observed_rowid'))<=?
               ORDER BY created_at DESC LIMIT 3""", (scene_id, after_rowid, through_rowid),
        )).fetchall()
        return [{"attempt_id": ref_id, "status": "not_committed", "reason": payload["error"][:600],
                 "source_event_ids": payload.get("source_event_ids", []),
                 "proposals": [{key: proposal.get(key) for key in
                     ("operation", "goal", "description", "job_id", "source_event_ids")}
                     for proposal in (payload["conversation"].get("staged_proposals") or
                         payload["conversation"].get("proposed_outcome", {}).get("job_proposals", []))]}
                for ref_id, raw in rows for payload in [json.loads(raw)]]

    async def own_sent_message_ids(self, actor_id: str, limit: int = 1000) -> list[str]:
        cursor = await self._db.execute(
            "SELECT json_extract(payload, '$.message_id') FROM events "
            "WHERE event_type = ? AND actor_id = ? AND json_extract(payload, '$.message_id') IS NOT NULL "
            "ORDER BY rowid DESC LIMIT ?", (EventType.MESSAGE_SENT.value, actor_id, limit),
        )
        return [str(row[0]) for row in reversed(await cursor.fetchall())]

    async def get_recent_events(self, scene_id, limit=50, through_rowid=None, *, conversation_only=False):
        cursor=await self._db.execute("""
            SELECT rowid,id,event_type,scene_id,actor_id,timestamp,payload,metadata FROM (
                SELECT rowid,* FROM events WHERE scene_id=? AND (? IS NULL OR rowid<=?)
                AND (?=0 OR COALESCE(json_extract(metadata,'$.conversation_excluded'),0)=0)
                ORDER BY rowid DESC LIMIT ?) ORDER BY rowid""",
            (scene_id,through_rowid,through_rowid,int(conversation_only),limit))
        return [Event(id=r[1],event_type=r[2],scene_id=r[3],actor_id=r[4],timestamp=r[5],
            payload=json.loads(r[6]),metadata={**json.loads(r[7]),'_rowid':r[0]}) for r in await cursor.fetchall()]

    async def get_events_since(self, scene_id: str, after_rowid: int = 0, limit: int = 200, event_types: list[EventType] | None = None, *, conversation_only=False) -> list[Event]:
        """ADR-0019 §10.4: events after a reflection cursor, in immutable write order.
        Each event's metadata carries its `_rowid` so callers can advance the cursor."""
        if not self._db:
            raise RuntimeError("Database not initialized")
        type_filter = ""
        if conversation_only:
            type_filter = " AND COALESCE(json_extract(metadata,'$.conversation_excluded'),0)=0"
        params = [scene_id, after_rowid]
        if event_types:
            type_filter += " AND event_type IN (" + ",".join("?" for _ in event_types) + ")"
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

    @staticmethod
    def _retrieval_event(row):
        return {"id": row[0], "event_type": row[1], "scene_id": row[2], "actor_id": row[3],
                "timestamp": row[4], "payload": json.loads(row[5]), "metadata": {**json.loads(row[7]), "_rowid": row[6]}}

    async def search_messages(self, query, allowed_scopes, limit: int, through_rowid=None):
        if type(limit) is not int or limit < 1:
            raise ValueError('Message search requires a positive integer limit')
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
            [term,*allowed_scopes,through_rowid,through_rowid,limit])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def read_context(self, event_id, before: int, after: int, allowed_scopes, through_rowid=None):
        if type(before) is not int or type(after) is not int or before < 0 or after < 0:
            raise ValueError('Context neighbor counts must be nonnegative integers')
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
                ORDER BY rowid {order} LIMIT ?""",[target[2],target[6],through_rowid,through_rowid,limit])
            rows.extend(await cursor.fetchall())
        return [self._retrieval_event(r) for r in sorted([*rows,target],key=lambda r:r[6])]

    async def references_belong_to_scene(self, reference_ids, scene_id, through_event_rowid=None):
        if not reference_ids: return True
        ids=list(reference_ids);placeholders=",".join("?" for _ in ids)
        cursor=await self._db.execute(f"""SELECT id FROM events WHERE scene_id=?
            AND id IN ({placeholders}) AND (? IS NULL OR rowid<=?)""",
            [scene_id,*ids,through_event_rowid,through_event_rowid])
        return {r[0] for r in await cursor.fetchall()} == set(reference_ids)

    async def query_timeline(self, scene_id, start_time, end_time, allowed_scopes, limit: int, through_rowid=None):
        if type(limit) is not int or limit < 1:
            raise ValueError('Timeline reads require a positive integer limit')
        if not allowed_scopes or scene_id not in allowed_scopes: return []
        cursor=await self._db.execute("""
            SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
            WHERE scene_id=? AND timestamp>=? AND timestamp<=? AND (? IS NULL OR rowid<=?)
            ORDER BY rowid LIMIT ?""",[scene_id,start_time,end_time,through_rowid,through_rowid,limit])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def query_person_history(self, actor_id, allowed_scopes, limit: int, through_rowid=None):
        if type(limit) is not int or limit < 1:
            raise ValueError('Person history reads require a positive integer limit')
        if not allowed_scopes: return []
        placeholders=",".join("?" for _ in allowed_scopes)
        cursor=await self._db.execute(f"""
            SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events
            WHERE actor_id=? AND scene_id IN ({placeholders}) AND (? IS NULL OR rowid<=?)
            ORDER BY rowid DESC LIMIT ?""",[actor_id,*allowed_scopes,through_rowid,through_rowid,limit])
        return [self._retrieval_event(r) for r in await cursor.fetchall()]

    async def load_scene_session(self, scene_id):
        row = await (await self._db.execute(
            "SELECT state_json FROM scene_sessions WHERE scene_id=?", (scene_id,))).fetchone()
        return json.loads(row[0]) if row else None

    async def memory_subjects(self, scene_id, memory_ids):
        rows=await (await self._db.execute(
            'SELECT DISTINCT subject FROM memories WHERE scope=? AND id IN (SELECT value FROM json_each(?))',
            (scene_id,json.dumps(memory_ids)))).fetchall()
        return {row[0] for row in rows}

    async def scene_member_locators(self, scene_id, through_rowid):
        rows=await (await self._db.execute("""SELECT actor_id,id,payload FROM events WHERE rowid IN (
            SELECT MAX(rowid) FROM events WHERE scene_id=? AND rowid<=?
              AND event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED')
              AND actor_id LIKE 'user:%' GROUP BY actor_id) ORDER BY actor_id""",
            (scene_id,through_rowid))).fetchall()
        result=[]
        for actor_id,event_id,payload in rows:
            sender={}
            for field in ('nickname','card'):
                value=await (await self._db.execute("""SELECT json_extract(payload,?) FROM events
                    WHERE scene_id=? AND actor_id=? AND rowid<=? AND json_type(payload,?) IS NOT NULL
                    ORDER BY rowid DESC LIMIT 1""",
                    (f'$.sender.{field}',scene_id,actor_id,through_rowid,f'$.sender.{field}'))).fetchone()
                sender[field]=value[0] if value else None
            result.append({'actor_id':actor_id,'source_event_id':event_id,
                           'nickname':sender.get('nickname'),'card':sender.get('card')})
        return result

    async def event_actors(self, scene_id, event_ids):
        if not event_ids:
            return set()
        rows = await (await self._db.execute(
            'SELECT DISTINCT actor_id FROM events WHERE scene_id=? AND id IN (SELECT value FROM json_each(?))',
            (scene_id, json.dumps(list(event_ids))))).fetchall()
        return {row[0] for row in rows}

    async def pending_response_actors(self, scene_id, bot_actor_id, after_timestamp):
        """Short-lived in-flight response facts, never a confirmed interaction."""
        rows = await (await self._db.execute("""SELECT message.value FROM events c,
              json_each(c.payload,'$.outcome.message_proposals') message
            WHERE c.scene_id=? AND c.event_type='CONVERSATION_COMMITTED' AND c.timestamp>=?
              AND json_extract(c.metadata,'$.mode')='live'
              AND COALESCE(json_extract(c.payload,'$.output_kind'),'chat')='chat'
              AND NOT EXISTS (SELECT 1 FROM events r WHERE r.scene_id=c.scene_id AND r.actor_id=?
                AND r.event_type IN ('MESSAGE_SENT','MESSAGE_SEND_FAILED','ACTION_SHADOWED')
                AND c.id='turn:'||json_extract(r.payload,'$.batch_id')
                AND json_extract(r.payload,'$.batch_index')=CAST(message.key AS INTEGER))""",
            (scene_id, after_timestamp, bot_actor_id))).fetchall()
        actors=set()
        for row in rows:
            message=json.loads(row[0])
            actors.update(message.get('addressed_to',[]))
            if message.get('requester_qq_uid'):actors.add('user:'+message['requester_qq_uid'])
        return actors

    async def read_reply_actor(self, scene_id, message_id, read_event_ids):
        row = await (await self._db.execute("""SELECT actor_id FROM events WHERE scene_id=?
            AND CAST(json_extract(payload,'$.message_id') AS TEXT)=?
            AND id IN (SELECT value FROM json_each(?)) ORDER BY rowid DESC LIMIT 1""",
            (scene_id, str(message_id), json.dumps(list(read_event_ids))))).fetchone()
        return row[0] if row else None

    async def events_by_ids(self, scene_id, event_ids, through_rowid):
        rows = await (await self._db.execute(
            'SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata FROM events '
            'WHERE scene_id=? AND rowid<=? AND id IN (SELECT value FROM json_each(?)) ORDER BY rowid',
            (scene_id, through_rowid, json.dumps(list(event_ids))))).fetchall()
        return [Event.model_validate(self._retrieval_event(row)) for row in rows]

    @staticmethod
    def _group_message_query(scene_id, start_at, end_at, cutoff_rowid, bot_actor_id):
        if not scene_id.startswith('group:') or start_at >= end_at or cutoff_rowid < 0:
            raise ValueError('A group window needs a valid group, half-open interval and snapshot')
        where = """scene_id=? AND event_type='GROUP_MESSAGE_RECEIVED'
            AND actor_id LIKE 'user:%' AND actor_id!=? AND timestamp>=? AND timestamp<?
            AND rowid<=? AND COALESCE(json_extract(metadata,'$.simulated'),0)=0"""
        return where, [scene_id, bot_actor_id, start_at, end_at, cutoff_rowid]

    async def group_message_statistics(self, scene_id, *, start_at, end_at, cutoff_rowid, bot_actor_id):
        where, parameters = self._group_message_query(scene_id, start_at, end_at, cutoff_rowid, bot_actor_id)
        row = await (await self._db.execute(f"""SELECT COUNT(*),COUNT(DISTINCT actor_id),
            COALESCE(SUM(LENGTH(COALESCE(NULLIF(json_extract(payload,'$.raw_text'),''),
              json_extract(payload,'$.content'),''))),0) FROM events WHERE {where}""", parameters)).fetchone()
        return dict(zip(('message_count', 'participant_count', 'character_count'), row))

    async def group_message_window(self, scene_id, *, start_at, end_at, cutoff_rowid, bot_actor_id, after_rowid, limit):
        where, parameters = self._group_message_query(scene_id, start_at, end_at, cutoff_rowid, bot_actor_id)
        if after_rowid < 0 or limit < 1:
            raise ValueError('A message page needs a nonnegative cursor and positive limit')
        rows = await (await self._db.execute(f"""SELECT id,event_type,scene_id,actor_id,timestamp,payload,rowid,metadata
            FROM events WHERE {where} AND rowid>? ORDER BY rowid ASC LIMIT ?""",
            [*parameters, after_rowid, limit])).fetchall()
        return [Event.model_validate(self._retrieval_event(row)) for row in rows]

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
                "UPDATE tasks SET status = ? WHERE id = ? AND scene_id = ? AND status = 'claimed' AND trigger_event_id = ?;",
                ("processing", task_id_to_trigger, event.scene_id, event.payload.get("trigger_event_id") or event.id)
            )

        await self._db.execute("DELETE FROM pending_runtime_events WHERE id=? AND scene_id=?",
                               (event.id, event.scene_id))
        resumed=event.metadata.get('conversation_resume')
        if resumed:
            changed=await self._db.execute("""UPDATE open_loops SET status='resolved'
                WHERE id=? AND scene_id=? AND source_event_id=? AND status='active' AND expires_at>?""",
                (resumed['loop_id'],event.scene_id,resumed['send_event_id'],self.clock()))
            if changed.rowcount!=1:
                raise ValueError('The sent wait was already consumed, expired or changed')
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
        # Event ingestion owns scanning; model/operator commits do not consume
        # or rescan inputs. The rowid and wake enqueue persist together.
        if event.event_type != EventType.CONVERSATION_COMMITTED:
            persisted['attention_scanned_event_rowid'] = event_rowid
            for wake in persisted.get('pending_wakes', []):
                if wake['event_id'] == event.id:
                    wake['rowid'] = event_rowid
        await self._db.execute(
            "INSERT INTO scene_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(scene_id) DO UPDATE SET "
            "version=excluded.version,last_observed_event_rowid=excluded.last_observed_event_rowid,"
            "attention_scanned_event_rowid=excluded.attention_scanned_event_rowid,state_json=excluded.state_json,updated_at=excluded.updated_at",
            (event.scene_id, persisted.get("version", 0), persisted.get("last_observed_event_rowid", 0),
             persisted.get("attention_scanned_event_rowid", 0), json.dumps(persisted, ensure_ascii=False), self.clock()))

        return event_rowid

    async def get_active_open_loops(self, scene_id: str) -> list[dict[str, Any]]:
        if not self._db:
            raise RuntimeError("Database not initialized")
        cursor = await self._db.execute(
            """
            SELECT l.id,l.scene_id,l.target_actor_id,l.intent,l.source_event_id,l.source_stimulus_id,l.status,l.created_at,l.expires_at,
                   json_extract(e.metadata,'$.associated_open_loop.resume_state'),json_extract(e.payload,'$.message_id')
            FROM open_loops l JOIN events e ON e.id=l.source_event_id AND e.scene_id=l.scene_id
            WHERE l.scene_id = ? AND l.status = 'active'
            ORDER BY l.created_at ASC;
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
                "resume_state":json.loads(r[9]) if r[9] is not None else None,
                "message_id":str(r[10]) if r[10] is not None else None,
            }
            for r in rows
        ]

    async def mark_suspended_conversations_for_review(self):
        """A new process cannot silently resume an old conversation run."""
        async with self._write_lock:
            await self._db.execute("""UPDATE open_loops SET status='review_required' WHERE status='active'
                AND EXISTS (SELECT 1 FROM events e WHERE e.id=open_loops.source_event_id
                  AND e.scene_id=open_loops.scene_id
                  AND json_type(e.metadata,'$.associated_open_loop.resume_state')='object')""")
            await self._db.commit()

    async def interrupt_plugin_waits_and_reminders(self,plugin_id,scene_id=None):
        """Pause owned waits and unsent reminders, retaining their source events."""
        changed={'wait_ids':[],'reminder_ids':[]}
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                rows=await (await self._db.execute("""SELECT l.id,l.scene_id,json_extract(e.payload,'$.plugin_origin')
                    FROM open_loops l JOIN events e ON e.id=l.source_event_id AND e.scene_id=l.scene_id
                    WHERE l.status='active' AND (? IS NULL OR l.scene_id=?)""",(scene_id,scene_id))).fetchall()
                for ident,scene,encoded in rows:
                    if encoded and PluginOrigin.model_validate_json(encoded).depends_on(plugin_id):
                        await self._db.execute("UPDATE open_loops SET status='review_required' WHERE id=? AND scene_id=?",(ident,scene))
                        changed['wait_ids'].append(ident)
                rows=await (await self._db.execute("""SELECT id,scene_id,payload FROM tasks
                    WHERE status IN ('pending','claimed','processing')
                    AND COALESCE(json_extract(payload,'$.kind'),'reminder')!='agent_job'
                    AND (? IS NULL OR scene_id=?)""",(scene_id,scene_id))).fetchall()
                for ident,scene,encoded in rows:
                    owner=json.loads(encoded).get('plugin_origin')
                    if owner and PluginOrigin.model_validate(owner).depends_on(plugin_id):
                        await self._db.execute("UPDATE tasks SET status='review_required' WHERE id=? AND scene_id=?",(ident,scene))
                        changed['reminder_ids'].append(ident)
                await self._db.commit()
                return changed
            except BaseException:
                await self._db.rollback()
                raise

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

    async def preview_diana_persona(self, *, palette_limit: int) -> dict:
        """Fill an operator draft; reading a template never changes saved values."""
        from len_bot.cognition.diana import PERSONA, MEDIA_REF_TAGS, build_examples
        palette = await self.list_palette("global-safe", limit=palette_limit)
        media_refs = {
            name: asset["id"]
            for name, tag in MEDIA_REF_TAGS.items()
            if (asset := next((item for item in palette if tag in item["tags"]), None)) is not None
        }
        examples = build_examples(media_refs)
        return {
            "fields": dict(PERSONA),
            "examples": examples,
            "missing_media": sorted({MEDIA_REF_TAGS[name] for item in examples
                                     for name in item["missing_media_refs"]}),
        }

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
            if part.type == 'at':
                raise ValueError('表达样例只保存文字与运营图片；真实成员提及由本轮实际对象决定')
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

    async def select_voice_examples(self, scene_id: str, query: str = "") -> list[dict[str, Any]]:
        """Enabled operator examples, with deterministic local context relevance."""
        examples = await self.list_voice_examples(scene_id)
        candidates = [item for item in examples if item["enabled"] and item["available"]]
        terms = set(re.findall(r"[\w\u4e00-\u9fff]+", (query or "").casefold()))
        def rank(item):
            text = " ".join((item.get("context", ""), item.get("tag", ""), item.get("content", ""))).casefold()
            hits = sum(1 for term in terms if term and term in text)
            return (-hits, 0 if item["scene_id"] == scene_id else 1, item["scene_id"], item["id"])
        return sorted(candidates, key=rank)

    async def save_task(self, task: TaskItem) -> None:
        """Persist one task using the current task contract."""
        async with self._write_lock:
            wake_match_json = json.dumps(task.wake_match, ensure_ascii=False) if task.wake_match is not None else None
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
                    task.id,
                    task.scene_id,
                    task.description,
                    task.due_at,
                    task.status.value,
                    task.source_event_id,
                    json.dumps(task.payload, ensure_ascii=False),
                    task.created_at,
                    task.wake_event_type,
                    wake_match_json,
                    task.origin_episode_id,
                    task.origin_stimulus_id,
                    task.trigger_event_id,
                    task.origin_mode,
                )
            )
            await self._db.commit()

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

    async def get_pending_tasks(self, max_due_at: Optional[float] = None) -> list[TaskItem]:
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

        return [
            TaskItem(
                id=r[0], scene_id=r[1], description=r[2], due_at=r[3],
                status=TaskStatus(r[4]), source_event_id=r[5], payload=json.loads(r[6]),
                created_at=r[7], wake_event_type=r[8],
                wake_match=json.loads(r[9]) if r[9] is not None else None,
                origin_episode_id=r[10], origin_stimulus_id=r[11],
                trigger_event_id=r[12], origin_mode=r[13],
            )
            for r in rows
        ]


    async def mark_task_status(self, task_id: str, status: TaskStatus, trigger_event_id: Optional[str] = None) -> None:
        async with self._write_lock:
            if trigger_event_id is not None:
                await self._db.execute(
                    "UPDATE tasks SET status = ?, trigger_event_id = ? WHERE id = ?;",
                    (status.value, trigger_event_id, task_id)
                )
            else:
                await self._db.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?;",
                    (status.value, task_id)
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

    async def commit_proposal_transaction(
        self,
        episode_id: str,
        scene_id: str,
        outcome: EpisodeOutcome,
        deliveries: dict[str, str] | None = None,
        acknowledgements: dict[str, str] | None = None,
        operation_confirmations: dict[str, str] | None = None,
        scene_commit: dict | None = None,
        origin_mode="live",
        bot_actor_id: str = "",
        through_rowid: int | None = None,
    ) -> tuple[list[Any], list[str], list[Any], EpisodeOutcome]:
        """
        V2 Atomic Proposal Commit (ADR-0003 & ADR-0011 Closure):
        Executes an all-or-nothing atomic durable commit for an EpisodeOutcome
        within a single SQLite transaction under write_lock.
        If any mutation fails, the entire transaction is rolled back.
        Returns the committed objects and the same resolved outcome written to
        the conversation event. A rejected transaction never mutates its input.
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        import uuid
        from len_bot.memory.models import MemoryItem
        from len_bot.cognition.models import CONDITION_TASK_DEFAULT_DEADLINE_SECONDS

        now = self.clock()
        committed_tasks: list[TaskItem] = []
        resolved_loop_ids: list[str] = []
        committed_memories: list[MemoryItem] = []
        resolved_outcome = outcome.model_copy(deep=True)
        task_proposals = resolved_outcome.task_proposals
        job_proposals = resolved_outcome.job_proposals
        memory_proposals = resolved_outcome.memory_proposals
        job_messages = resolved_outcome.message_proposals
        resolve_open_loop_ids = resolved_outcome.resolve_open_loop_ids
        operation_confirmations = operation_confirmations or {}
        if origin_mode == "shadow":
            for proposal in task_proposals:
                proposal.origin_mode = "shadow"

        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                operation_proposals = {}
                all_refs = set()
                creation_refs = set()
                for kind, proposals in (("work",job_proposals),("reminder",task_proposals),("memory",memory_proposals)):
                    for proposal in proposals:
                        reference = proposal.proposal_id
                        if reference:
                            if reference in all_refs:
                                raise ValueError("Each transaction proposal needs a distinct turn-local reference")
                            all_refs.add(reference)
                            if kind == "memory" or proposal.operation != "create":
                                operation_proposals[reference] = (kind,proposal)
                            else:
                                creation_refs.add(reference)
                if not set(acknowledgements or {}).issubset(creation_refs):
                    raise ValueError("Creation acknowledgement cannot confirm a control or memory operation")
                if not set(operation_confirmations).issubset(operation_proposals):
                    raise ValueError("Operation confirmation does not refer to this transaction's control or memory proposal")
                confirmation_refs=[message.operation_ref for message in job_messages if message.operation_ref]
                if len(confirmation_refs)!=len(set(confirmation_refs)) or set(confirmation_refs)!=set(operation_confirmations):
                    raise ValueError("Each operation confirmation must have exactly one matching action")
                if operation_confirmations and scene_commit is None:
                    raise ValueError("Operation confirmation requires a committed conversation event")
                observed_jobs = await self.validate_job_proposals_in_transaction(job_proposals,scene_id)
                task_states = {}
                for tp in task_proposals:
                    if tp.payload.get("kind") == "agent_job":
                        raise ValueError("Use typed job_proposals for information work")
                    if tp.operation not in {"create", "update", "cancel", "result", "fail"}:
                        raise ValueError("Unknown task operation")
                    if tp.source_event_ids:
                        placeholders = ",".join("?" for _ in set(tp.source_event_ids))
                        evidence = await self._db.execute(
                            f"SELECT count(*) FROM events WHERE scene_id=? AND id IN ({placeholders})",
                            [scene_id, *set(tp.source_event_ids)])
                        if (await evidence.fetchone())[0] != len(set(tp.source_event_ids)):
                            raise ValueError("Task evidence outside scene")
                    if tp.operation == "create":
                        if not tp.description.strip():
                            raise ValueError("Task description is empty")
                        request = await (await self._db.execute(
                            'SELECT event_type,actor_id FROM events WHERE id=? AND scene_id=?',
                            (tp.request_source_event_id,scene_id))).fetchone()
                        if (request is None or request[0] not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                                or request[1] == bot_actor_id or not request[1].startswith('user:')
                                or request[1] != tp.requester_id
                                or request[1].removeprefix('user:') != tp.payload.get('requester_qq_uid')
                                or tp.request_source_event_id not in tp.source_event_ids):
                            raise ValueError('Reminder requester must match its explicit human request source')
                        if tp.due_at is not None and tp.due_at < now:
                            raise ValueError("Task time is already past; review the promise instead")
                        continue
                    if tp.task_id in task_states:
                        raise ValueError("One transaction cannot control the same reminder more than once")
                    existing = await (await self._db.execute(
                        "SELECT status,payload,due_at FROM tasks WHERE id=? AND scene_id=?",
                        (tp.task_id, scene_id))).fetchone()
                    if existing is None:
                        raise ValueError("Task not found in this scene")
                    status, payload, due_at = existing[0], json.loads(existing[1]), existing[2]
                    if payload.get("kind") == "agent_job":
                        raise ValueError("Use versioned job controls for information work")
                    if status not in {"pending", "claimed", "processing", "review_required", "result_ready"}:
                        raise ValueError("Task no longer editable")
                    if tp.operation == "update" and (tp.due_at is None or tp.due_at < now):
                        raise ValueError("Task update requires absolute due_at")
                    if tp.operation == "result" and not tp.result:
                        raise ValueError("Task result cannot be empty")
                    task_states[tp.task_id] = (status,payload,due_at)
                targets = [ident for mp in memory_proposals for ident in mp.target_memory_ids]
                if len(targets) != len(set(targets)):
                    raise ValueError("One transaction cannot change the same memory target more than once")
                for mp in memory_proposals:
                    await validate_memory_proposal(self._db, mp, scene_id, through_rowid, bot_actor_id=bot_actor_id)
                    if mp.operation != "refute" and mp.expires_at is not None and mp.expires_at <= now:
                        raise ValueError("An already expired preference or belief cannot become active")
                for message in job_messages:
                    for segment in message.segments:
                        if segment.type in {"image", "video", "audio"} and await self.get_media(segment.asset_id, [scene_id, "global-safe"]) is None:
                            raise ValueError("Message media is disabled or outside the scene")
                    if message.operation_ref:
                        if message.operation_ref not in operation_confirmations:
                            raise ValueError("Operation confirmation is missing its actual action binding")
                        kind, proposal = operation_proposals[message.operation_ref]
                        sources = proposal.evidence if kind == "memory" else proposal.source_event_ids
                        original = await (await self._db.execute(
                            "SELECT event_type,actor_id FROM events WHERE id=? AND scene_id=?",
                            (message.source_event_id,scene_id))).fetchone()
                        if (message.source_event_id not in sources or original is None
                                or original[0] not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                                or original[1] == bot_actor_id or original[1] != 'user:' + (message.requester_qq_uid or '')
                                or message.source_event_id not in scene_commit['event'].payload['source_event_ids']):
                            raise ValueError("Operation confirmation must retain its own actual human request source")
                        if kind == "work":
                            if message.job_id != proposal.job_id or message.job_revision != proposal.expected_revision:
                                raise ValueError("Work operation confirmation must use that operation's observed work and revision")
                        elif message.job_id is not None:
                            raise ValueError("A reminder or memory confirmation cannot claim another work")
                    elif message.job_id in observed_jobs:
                        raise ValueError("A controlled work cannot also send old work content or fulfil an old result in the same transaction")
                    if message.fulfils_task_id in task_states:
                        raise ValueError("A changed reminder cannot also fulfil its previous state in the same transaction")
                    if message.job_id:
                        linked_job=await self.validate_job_message(scene_id, message.job_id, message.job_revision, bool(message.fulfils_task_id))
                        prepared=(linked_job['result'] or {}).get('delivery')
                        if (message.fulfils_task_id and prepared
                                and [part.model_dump(mode='json') for part in message.segments]!=prepared['segments']):
                            raise ValueError('Prepared work delivery must preserve its saved message segments')
                    elif message.fulfils_task_id and await self.get_job(message.fulfils_task_id, scene_id):
                        raise ValueError("Job delivery requires job_id and job_revision")

                # 2. Insert Tasks
                job_tasks, proposal_tasks = await self.apply_job_proposals_in_transaction(job_proposals, scene_id, episode_id, origin_mode, observed_jobs)
                committed_tasks.extend(job_tasks)
                for tp in task_proposals:
                    if tp.operation != "create":
                        if tp.proposal_id:
                            proposal_tasks[tp.proposal_id] = tp.task_id
                        status, payload, due_at = task_states[tp.task_id]
                        if tp.operation == "update":
                            status, due_at = "pending", tp.due_at
                            payload.pop("result", None)
                        elif tp.operation == "cancel":
                            status = "cancelled"
                        elif tp.operation == "fail":
                            status = "failed"
                            payload["error"] = tp.result or tp.description
                        else:
                            status = "result_ready"
                            payload["result"] = tp.result
                        await self._db.execute(
                            "UPDATE tasks SET status=?,due_at=?,description=CASE WHEN ?='' THEN description ELSE ? END,payload=?,"
                            "origin_mode=CASE WHEN ?='shadow' THEN 'shadow' ELSE origin_mode END WHERE id=? AND scene_id=?",
                            (status, due_at, tp.description, tp.description, json.dumps(payload, ensure_ascii=False), tp.origin_mode, tp.task_id, scene_id),
                        )
                        continue
                    tp.payload = {**tp.payload, "requester_id": tp.requester_id,
                                  "request_source_event_id": tp.request_source_event_id,
                                  "target_actor_id": tp.target_actor_id,
                                  "source_event_ids": tp.source_event_ids,
                                  "proposal_id": tp.proposal_id}
                    # A stable source + proposal id makes retries of the same proposal idempotent.
                    if tp.proposal_id:
                        prior = await self._db.execute(
                            """SELECT id,status,payload FROM tasks WHERE scene_id=?
                               AND json_extract(payload,'$.proposal_id')=?
                               AND json_extract(payload,'$.request_source_event_id')=?""",
                            (scene_id, tp.proposal_id, tp.request_source_event_id),
                        )
                        previous = await prior.fetchone()
                        if previous:
                            if previous[1] != "pending":
                                raise ValueError("This request already has a non-pending task; inspect it instead of confirming creation")
                            prior_payload=json.loads(previous[2])
                            if (prior_payload.get('requester_id') != tp.requester_id
                                    or prior_payload.get('target_actor_id') != tp.target_actor_id):
                                raise ValueError('Existing reminder reference belongs to a different request relationship')
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
                    cursor = await self._db.execute(
                        "UPDATE tasks SET payload=json_set(payload,'$.ack_action_id',?) WHERE id=? AND scene_id=? "
                        "AND json_extract(payload,'$.ack_action_id') IS NULL",
                        (action_id, proposal_tasks[reference], scene_id))
                    if cursor.rowcount != 1:
                        raise ValueError('This request already has an acknowledgement action; no second confirmation was committed')

                # 3. Resolve Open Loops (ADR-0025: strictly scoped to this scene)
                for loop_id in resolve_open_loop_ids:
                    cursor = await self._db.execute(
                        "UPDATE open_loops SET status = 'resolved' WHERE id = ? AND scene_id = ? AND status IN ('active','review_required');",
                        (loop_id, scene_id)
                    )
                    if cursor.rowcount == 0:
                        raise ValueError(f"Cannot resolve open loop '{loop_id}': not active or does not belong to scene '{scene_id}'")
                    resolved_loop_ids.append(loop_id)

                # 4. Commit Memory Proposals with Unified Validation & Conflict Resolution (ADR-0025, §13)
                for mp in memory_proposals:
                    mem_item = await commit_memory_proposal_core(self._db, mp, scene_id, now=now,
                        revision_event_id=scene_commit["event"].id if scene_commit else None)
                    committed_memories.append(mem_item)

                memory_results = {proposal.proposal_id:item for proposal,item in zip(memory_proposals,committed_memories)
                                  if proposal.proposal_id}
                operation_receipts = {}
                for reference,(kind,proposal) in operation_proposals.items():
                    reminder_values = {}
                    if kind == "memory":
                        item = memory_results[reference]
                        target_id, revision, status = item.id, item.revision, item.status.value
                        sources = proposal.evidence
                    elif kind == "work":
                        job = await self.get_job(proposal.job_id,scene_id)
                        target_id, revision, status = job["id"], job["revision"], job["status"]
                        sources = proposal.source_event_ids
                    else:
                        row = await (await self._db.execute("SELECT id,status,due_at,description FROM tasks WHERE id=? AND scene_id=?",
                            (proposal.task_id,scene_id))).fetchone()
                        target_id, revision, status = row[0], None, row[1]
                        reminder_values = {'reminder_due_at':row[2],'reminder_description':row[3]}
                        sources = proposal.source_event_ids
                    operation_receipts[reference] = OperationReceipt(proposal_ref=reference,kind=kind,
                        operation=proposal.operation,target_id=target_id,revision=revision,result_status=status,
                        source_event_ids=sources,action_id=operation_confirmations.get(reference),**reminder_values)
                for message in job_messages:
                    if message.operation_ref:
                        receipt = operation_receipts[message.operation_ref]
                        if receipt.kind == "work":
                            message.job_revision = receipt.revision
                    elif message.task_ref:
                        task_id = proposal_tasks[message.task_ref]
                        job = await self.get_job(task_id, scene_id)
                        if job:
                            message.job_id, message.job_revision = job['id'], job['revision']
                for task_id, action_id in (deliveries or {}).items():
                    cursor = await self._db.execute(
                        """UPDATE tasks SET status='awaiting_delivery',
                           payload=json_set(payload,'$.delivery_action_id',?)
                           WHERE id=? AND scene_id=? AND status IN ('processing','result_ready')
                           AND json_extract(payload,'$.delivery_action_id') IS NULL
                           AND (COALESCE(json_extract(payload,'$.kind'),'reminder') != 'query'
                                OR json_extract(payload,'$.result') IS NOT NULL)""",
                        (action_id, task_id, scene_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("Task is not ready for fulfilment")
                for source in resolved_outcome.source_outcomes:
                    source.task_ids=list(dict.fromkeys(
                        [proposal_tasks[ref] for ref in source.proposal_refs if ref in proposal_tasks]
                        +[operation_receipts[ref].target_id for ref in source.proposal_refs
                          if ref in operation_receipts and operation_receipts[ref].kind in {'work','reminder'}]
                        +[ident for index,message in enumerate(job_messages) if index in source.message_indices
                          for ident in (message.job_id,message.fulfils_task_id) if ident]))

                if scene_commit is not None:
                    scene_commit["event"].payload["outcome"] = resolved_outcome.model_dump(mode="json")
                    scene_commit["event"].payload["source_outcomes"] = [source.model_dump(mode='json') for source in resolved_outcome.source_outcomes]
                    scene_commit["event"].payload["operation_receipts"] = {
                        reference:receipt.model_dump(mode="json") for reference,receipt in operation_receipts.items()}
                    scene_commit["event"].payload["memory_receipts"] = [
                        await self._memory_receipt(mp, item) for mp, item in zip(memory_proposals, committed_memories)]
                    await self._write_scene_event(**scene_commit)

                # 5. Commit all mutations atomically in one transaction!
                await self._db.commit()
                return committed_tasks, resolved_loop_ids, committed_memories, resolved_outcome
            except BaseException:
                await self._db.rollback()
                raise

    async def _memory_receipt(self, proposal, item):
        return {"id": item.id, "subject": item.subject, "kind": item.kind,
                "statement": item.statement, "basis": item.basis, "status": item.status,
                "revision":item.revision,"proposal_ref":proposal.proposal_id,
                "operation": proposal.operation, "reason": proposal.reason,
                "evidence": proposal.evidence, "target_memory_ids": proposal.target_memory_ids}

    async def validate_operation_message(self, action):
        """Confirm a durable operation receipt, without treating it as old work content."""
        if not action.operation_ref or not action.batch_id:
            raise ValueError("Operation confirmation has no committed turn reference")
        row = await (await self._db.execute(
            "SELECT payload FROM events WHERE id=? AND scene_id=? AND event_type='CONVERSATION_COMMITTED'",
            ('turn:' + action.batch_id,action.scene_id))).fetchone()
        if row is None:
            raise ValueError("Operation confirmation has no committed conversation")
        payload = json.loads(row[0])
        raw = payload.get('operation_receipts', {}).get(action.operation_ref)
        if raw is None:
            raise ValueError("Operation confirmation does not belong to this committed turn")
        receipt = OperationReceipt.model_validate(raw)
        if receipt.proposal_ref != action.operation_ref or receipt.action_id != action.id:
            raise ValueError("Operation confirmation action does not match its committed receipt")
        messages = [MessageProposal.model_validate(item) for item in payload['outcome']['message_proposals']
                    if item.get('operation_ref') == action.operation_ref]
        if len(messages) != 1:
            raise ValueError("Operation receipt must have one committed confirmation message")
        message = messages[0]
        if (message.source_event_id != action.origin_event_id or message.requester_qq_uid != action.requester_qq_uid
                or action.origin_event_id not in receipt.source_event_ids
                or message.job_id != action.job_id or message.job_revision != action.job_revision
                or message.reply_to != action.reply_to
                or [item.model_dump(mode='json') for item in message.segments] != [item.model_dump(mode='json') for item in action.segments]):
            raise ValueError("Operation confirmation changed its committed source, version or message")
        if receipt.kind == 'work':
            if receipt.target_id != action.job_id or receipt.revision != action.job_revision:
                raise ValueError("Operation receipt belongs to another work or revision")
            # Cancelled is a valid result of this operation. Only a later work
            # revision invalidates its confirmation; normal execution progress
            # after a resume does not undo the fact that it was resumed.
            await self.validate_job_message(action.scene_id,receipt.target_id,receipt.revision)
        elif receipt.kind == 'memory':
            current = await (await self._db.execute("SELECT revision,status FROM memories WHERE id=? AND scope=?",
                (receipt.target_id,action.scene_id))).fetchone()
            if current is None or current[0] != receipt.revision or current[1] != receipt.result_status:
                raise ValueError("Memory operation result changed before its confirmation was sent")
        else:
            current = await (await self._db.execute("SELECT status,due_at,description FROM tasks WHERE id=? AND scene_id=?",
                (receipt.target_id,action.scene_id))).fetchone()
            if (current is None or current[1] != receipt.reminder_due_at or current[2] != receipt.reminder_description
                    or receipt.operation == 'cancel' and current[0] != 'cancelled'
                    or receipt.operation != 'cancel' and current[0] == 'cancelled'):
                raise ValueError("Reminder operation result changed before its confirmation was sent")
        return receipt

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
        """Restore durable attention sources; delivery recovery stays in its ledger."""
        rows = await (await self._db.execute('SELECT scene_id FROM scene_sessions')).fetchall()
        return [row[0] for row in rows]

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
                    """SELECT t.id,t.scene_id,t.description,t.status,t.origin_mode,t.trigger_event_id,j.result_json
                       FROM tasks t LEFT JOIN agent_jobs j ON j.id=t.id AND j.scene_id=t.scene_id
                       WHERE t.status IN ('processing','claimed','result_ready','review_required')"""
                )
                for task_id, scene_id, description, status, origin_mode, trigger_event_id, work_result in await cursor.fetchall():
                    if status == 'claimed':
                        pending_due = await self._db.execute(
                            """SELECT 1 FROM pending_runtime_events
                               WHERE scene_id=? AND json_extract(event_json,'$.scene_id')=?
                               AND json_extract(event_json,'$.event_type')=?
                               AND json_extract(event_json,'$.payload.task_id')=?
                               AND COALESCE(NULLIF(json_extract(event_json,'$.payload.trigger_event_id'),''),id)=?""",
                            (scene_id, scene_id, EventType.TASK_DUE.value, task_id, trigger_event_id),
                        )
                        if await pending_due.fetchone():
                            # The original claim has not entered the scene yet. Preserve
                            # it so the pending TASK_DUE remains current when delivered.
                            continue
                    # A persisted execution result survives process restart.
                    # Only unfinished execution needs an explicit resume.
                    recovered_status = 'result_ready' if status == 'result_ready' or work_result is not None else 'review_required'
                    await self._db.execute("UPDATE tasks SET status=? WHERE id=? AND scene_id=?",
                                           (recovered_status, task_id, scene_id))
                    pending = await self._db.execute(
                        """SELECT 1 FROM pending_runtime_events
                           WHERE scene_id=? AND json_extract(event_json,'$.scene_id')=?
                           AND json_extract(event_json,'$.event_type')=?
                           AND json_extract(event_json,'$.payload.task_id')=?
                           AND json_extract(event_json,'$.payload.trigger_event_id') IS ?""",
                        (scene_id, scene_id, EventType.TASK_REVIEW.value, task_id, trigger_event_id),
                    )
                    if await pending.fetchone():
                        continue
                    event = Event(event_type=EventType.TASK_REVIEW, scene_id=scene_id,
                                  actor_id="system:recovery", payload={
                                      "task_id": task_id, "origin_mode": origin_mode,
                                      "trigger_event_id": trigger_event_id,
                                      "recovered_status": recovered_status,
                                      "raw_text": f"重启后待核对任务：{description}。检查当前时间和结果，不要假定已完成。",
                                  })
                    await self._db.execute("INSERT INTO pending_runtime_events VALUES (?,?,?)",
                                           (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
