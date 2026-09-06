"""RuntimeQueryService (ADR-0022): the single read facade over the runtime.

Control Plane routes NEVER reach into runtime internals (`event_store._db`,
`scene_manager._actors`, `plugin_host._plugins`) — every read goes through
here, so the Dashboard is decoupled from Runtime implementation details.
Mutating interventions use explicit operator events and proposal submission.
"""

import json
import time
from typing import Optional
from len_bot.scenes.models import SceneSession
from len_bot.memory.store import MEMORY_COLUMNS, memory_from_row


class RuntimeQueryService:
    def __init__(self, runtime):
        self.runtime = runtime

    def current_time(self) -> float:
        return self.runtime.event_store.clock()

    async def list_voice_examples(self, scene_id=None):
        return await self.runtime.event_store.list_voice_examples(scene_id)

    async def tool_results(self, scene_id, limit=100):
        return await self.runtime.event_store.list_tool_observations(scene_id, limit)

    async def tool_result(self, scene_id, result_id, offset=0):
        result = await self.runtime.event_store.read_tool_observation(result_id, [scene_id])
        return result.page(offset).model_dump() if result else None

    async def jobs(self, scene_id=None):
        return await self.runtime.event_store.list_jobs(scene_id)

    async def job(self, job_id):
        task = await self.get_task(job_id)
        return await self.runtime.event_store.get_job(job_id, task["scene_id"]) if task else None

    async def media_assets(self, scene_id, query=""):
        rows = await self.runtime.event_store.list_media(list(dict.fromkeys([scene_id, "global-safe"])), query=query, include_disabled=True)
        return [{key: asset[key] for key in ("id", "scope", "source_event_id", "mime_type", "description", "tags", "enabled", "curated", "created_at", "palette_order")} for asset in rows]

    async def media_file(self, asset_id, scene_id):
        return await self.runtime.media_service.get_bytes(asset_id, scene_id, include_disabled=True)

    def persona_settings(self):
        return {key:getattr(self.runtime.config,key) for key in (
            "character_context","identity_name","identity_core","identity_persona","conversation_style","bot_qq")}

    async def preview_diana_persona(self):
        return await self.runtime.event_store.preview_diana_persona()

    # ---------- Overview ----------

    async def overview(self) -> dict:
        rt = self.runtime
        stats = await rt.event_store.get_stats()

        memory_count = 0
        if rt.memory_store:
            cursor = await rt.memory_store._db.execute(
                "SELECT COUNT(*) FROM memories WHERE status='active' AND (expires_at IS NULL OR expires_at>?)", (self.current_time(),))
            (memory_count,) = await cursor.fetchone()

        scenes = await self.list_scenes()
        routing = rt.provider_registry.snapshot()
        social = rt.metrics.snapshot()["social"]

        return {
            "stats": {
                **stats,
                "active_scenes": len(scenes),
                "memory_beliefs_count": memory_count,
                "websocket_connected": self.websocket_connected(),
                "onebot_connection_mode": rt.config.onebot_connection_mode,
                "conversation_model": routing["routing"]["conversation"]["model"] if routing["routing"] else None,
                "work_model": routing["routing"]["work"]["model"] if routing["routing"] else None,
                "identity_name": rt.config.identity_name,
                "bot_qq": rt.config.bot_qq,
                "uptime_seconds": time.time() - getattr(rt, "_started_at", time.time()),
                "shadow_mode": rt.shadow_mode,
            },
            "scenes": scenes,
            "social_metrics": social,
        }

    def websocket_connected(self) -> bool:
        adapter = getattr(self.runtime, "_onebot_adapter", None)
        return bool(adapter and adapter.connected)

    def onebot_status(self) -> dict:
        adapter = getattr(self.runtime, "_onebot_adapter", None)
        if adapter:
            return adapter.status()
        config = self.runtime.config
        return {
            "connection_mode": config.onebot_connection_mode,
            "action_transport": config.onebot_action_transport,
            "ws_url": config.onebot_ws_url,
            "http_url": config.onebot_http_url,
            "host": config.ws_host,
            "port": config.ws_port,
            "connected": False,
            "remote_address": None,
            "server_status": "stopped",
            "connector_status": "stopped",
            "last_error": None,
            "access_token_set": bool(config.onebot_access_token),
            "echo_counter": 0,
        }

    # ---------- Scenes ----------

    async def list_scenes(self) -> list[dict]:
        rows = await (await self.runtime.event_store._db.execute(
            "SELECT state_json FROM scene_sessions ORDER BY updated_at DESC,scene_id")).fetchall()
        jobs = await self.jobs()
        active_statuses = {"pending", "claimed", "processing", "review_required", "result_ready", "awaiting_delivery"}
        counts: dict[str, int] = {}
        for job in jobs:
            if job["status"] in active_statuses:
                counts[job["scene_id"]] = counts.get(job["scene_id"], 0) + 1
        sessions = [SceneSession.model_validate_json(row[0]) for row in rows]
        return [{"scene_id": session.scene_id, "version": session.version,
                 "participant_count": len(session.participants), "last_event_at": session.last_event_at,
                 "last_bot_message_at": session.last_bot_message_at,
                 "active_job_count": counts.get(session.scene_id, 0)} for session in sessions]

    async def scene_detail(self, scene_id: str) -> Optional[dict]:
        """Read the committed fact session without creating or changing an Actor."""
        raw_session = await self.runtime.event_store.load_scene_session(scene_id)
        if raw_session is None:
            return None
        session = SceneSession.model_validate(raw_session)
        preferences = await self.runtime.memory_store.interaction_preferences(scene_id, list(session.participants)) if self.runtime.memory_store else []
        recent = await self.query_events(scene_id=scene_id, limit=160)
        return {
            "session": session.model_dump(mode="json"),
            "preferences": [item.model_dump(mode="json") for item in preferences],
            "jobs": await self.jobs(scene_id),
            "recent_messages": [event for event in recent if event["event_type"] in {
                "GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "MESSAGE_SENT"}][:40],
            "recent_deliveries": [event for event in recent
                                  if event["event_type"] in {"MESSAGE_SENT", "MESSAGE_SEND_FAILED", "ACTION_SHADOWED"}][:12],
        }

    # ---------- Events / Tasks / Loops / Memories ----------

    async def query_events(
        self,
        scene_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: int = 50,
    ) -> list[dict]:
        sql = "SELECT id, event_type, scene_id, actor_id, timestamp, payload FROM events WHERE 1=1"
        params: list = []
        if scene_id:
            sql += " AND scene_id = ?"
            params.append(scene_id)
        if actor_id:
            sql += " AND actor_id = ?"
            params.append(actor_id)
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        if until is not None:
            sql += " AND timestamp <= ?"
            params.append(until)
        sql += " ORDER BY rowid DESC LIMIT ?;"
        params.append(max(1, min(limit, 1000)))
        cursor = await self.runtime.event_store._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "event_type": r[1], "scene_id": r[2], "actor_id": r[3],
             "timestamp": r[4], "payload": json.loads(r[5]) if r[5] else {}}
            for r in rows
        ]

    async def list_tasks(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        sql = "SELECT id, scene_id, description, due_at, status, payload, created_at, wake_event_type FROM tasks"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY due_at ASC LIMIT ?;"
        params.append(limit)
        cursor = await self.runtime.event_store._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "scene_id": r[1], "description": r[2], "due_at": r[3], "status": r[4],
             "payload": json.loads(r[5]) if r[5] else {}, "created_at": r[6], "wake_event_type": r[7]}
            for r in rows
        ]

    async def get_task(self, task_id: str) -> dict | None:
        cursor = await self.runtime.event_store._db.execute(
            "SELECT id,scene_id,description,due_at,status,payload,wake_event_type,wake_match_json FROM tasks WHERE id=?", (task_id,))
        row = await cursor.fetchone()
        return dict(id=row[0], scene_id=row[1], description=row[2], due_at=row[3], status=row[4],
                    payload=json.loads(row[5]), wake_event_type=row[6],
                    wake_match=json.loads(row[7]) if row[7] else None) if row else None

    async def open_loop(self, loop_id: str) -> dict | None:
        cursor = await self.runtime.event_store._db.execute(
            "SELECT id,scene_id,status FROM open_loops WHERE id=?", (loop_id,))
        row = await cursor.fetchone()
        return {"id": row[0], "scene_id": row[1], "status": row[2]} if row else None

    async def list_open_loops(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        sql = "SELECT id, scene_id, target_actor_id, intent, source_event_id, status, created_at, expires_at FROM open_loops"
        params: list = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)
        cursor = await self.runtime.event_store._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "scene_id": r[1], "target_actor_id": r[2], "intent": r[3],
             "source_event_id": r[4], "status": r[5], "created_at": r[6], "expires_at": r[7]}
            for r in rows
        ]

    async def list_memories(
        self,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        subject: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        if not self.runtime.memory_store:
            return []
        sql = f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        sql += " ORDER BY created_at DESC,id LIMIT ?;"
        params.append(max(1, min(limit, 200)))
        cursor = await self.runtime.memory_store._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [memory_from_row(row).model_dump(mode="json") for row in rows]

    async def memory(self, memory_id: str) -> dict | None:
        if not self.runtime.memory_store:
            return None
        row = await (await self.runtime.memory_store._db.execute(
            f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE id=?", (memory_id,))).fetchone()
        return memory_from_row(row).model_dump(mode="json") if row else None

    async def memory_chain(self, memory_id: str) -> list[dict]:
        """Include every merged predecessor, not just the first old semantic key."""
        if not self.runtime.memory_store:
            return []
        cursor = await self.runtime.memory_store._db.execute(
            f"""WITH RECURSIVE chain(id, scope, superseded_by) AS (
                   SELECT id,scope,superseded_by FROM memories WHERE id=?
                   UNION
                   SELECT m.id,m.scope,m.superseded_by FROM memories m JOIN chain c
                     ON m.scope=c.scope AND (m.id=c.superseded_by OR m.superseded_by=c.id)
               )
               SELECT {','.join('m.' + column for column in MEMORY_COLUMNS)} FROM memories m JOIN chain c ON m.id=c.id
               ORDER BY m.created_at,m.id""", (memory_id,))
        return [memory_from_row(row).model_dump(mode="json") for row in await cursor.fetchall()]

    # ---------- Trace / Metrics / Plugins / Shadow ----------

    async def query_traces(
        self,
        scene_id: Optional[str] = None,
        kind: Optional[str] = None,
        ref_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        return await self.runtime.event_store.query_traces(scene_id=scene_id, kind=kind, ref_id=ref_id, limit=limit)

    def metrics(self) -> dict:
        return self.runtime.metrics.snapshot()

    def plugins(self) -> list[dict]:
        return self.runtime.plugin_host.status_snapshot()

    async def provider_models(self, provider_id):
        return await self.runtime.provider_registry.list_models(provider_id)

    def providers(self) -> dict:
        return self.runtime.provider_registry.snapshot()

    def shadow_would_send(self, limit: int = 100) -> list[dict]:
        return list(self.runtime.shadow_would_send_log)[-limit:][::-1]

    def delivery_settings(self) -> dict:
        return {"enabled": self.runtime.shadow_mode,
                "allowed_scenes": sorted(self.runtime.allowed_scenes)}
