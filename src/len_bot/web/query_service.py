"""RuntimeQueryService (ADR-0022): the single read facade over the runtime.

Control Plane routes NEVER reach into runtime internals (`event_store._db`,
`scene_manager._actors`, `plugin_host._plugins`) — every read goes through
here, so the Dashboard is decoupled from Runtime implementation details.
Mutating interventions stay on their existing authority paths (scheduler,
OpenLoopManager, MemoryStore, receive_event).
"""

import json
import time
from typing import Optional
from len_bot.scenes.models import SceneState


class RuntimeQueryService:
    def __init__(self, runtime):
        self.runtime = runtime

    async def list_voice_examples(self, scene_id=None):
        return await self.runtime.event_store.list_voice_examples(scene_id)

    # ---------- Overview ----------

    async def overview(self) -> dict:
        rt = self.runtime
        stats = await rt.event_store.get_stats()

        memory_count = 0
        if rt.memory_store:
            cursor = await rt.memory_store._db.execute("SELECT COUNT(*) FROM memories WHERE status = 'active';")
            (memory_count,) = await cursor.fetchone()

        scenes = self.loaded_scene_summaries()
        routing = rt.provider_registry.snapshot()
        social = rt.metrics.snapshot()["social"]

        return {
            "stats": {
                **stats,
                "active_scenes": sum(1 for s in scenes if s["activity"] in ("active", "hot")),
                "memory_beliefs_count": memory_count,
                "websocket_connected": self.websocket_connected(),
                "onebot_connection_mode": rt.config.onebot_connection_mode,
                "normal_model": routing["routing"]["normal"]["model"] if routing["routing"] else None,
                "deliberate_model": routing["routing"]["deliberate"]["model"] if routing["routing"] else None,
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

    def loaded_scene_summaries(self) -> list[dict]:
        summaries = []
        for scene_id, actor in self.runtime.scene_manager._actors.items():
            state = actor.state
            session = actor.group_session
            summaries.append({
                "scene_id": scene_id,
                "version": state.version if state else 0,
                "activity": state.activity_level if state else "idle",
                "topics": [topic.model_dump(mode="json") for topic in session.social_world.topics] if session else [],
                "engagement": session.self_social_state.engagement if session else "observing",
                "consecutive_bot_messages": state.consecutive_bot_messages if state else 0,
            })
        return summaries

    async def list_scenes(self) -> list[dict]:
        scenes = []
        for scene_id, actor in self.runtime.scene_manager._actors.items():
            state = actor.state
            session = actor.group_session
            scenes.append({
                "scene_id": scene_id,
                "version": state.version if state else 0,
                "activity_level": state.activity_level if state else "idle",
                "participant_count": len(state.participants) if state else 0,
                "social_world": session.social_world.model_dump(mode="json") if session else None,
                "self_social_state": session.self_social_state.model_dump(mode="json") if session else None,
                "is_in_memory": True,
            })

        cursor = await self.runtime.event_store._db.execute("SELECT DISTINCT scene_id FROM events;")
        rows = await cursor.fetchall()
        existing_ids = {s["scene_id"] for s in scenes}
        for r in rows:
            if r[0] and r[0] not in existing_ids:
                scenes.append({
                    "scene_id": r[0], "version": 0, "activity_level": "idle",
                    "participant_count": 0, "social_world": None,
                    "self_social_state": None, "is_in_memory": False,
                })
        return scenes

    async def scene_detail(self, scene_id: str) -> Optional[dict]:
        """ADR-0027 (§22): Read-only scene detail without mutating actor registry."""
        state = self.runtime.scene_manager.get_scene_state(scene_id)
        if state is None:
            raw_state = await self.runtime.event_store.load_scene_state(scene_id)
            if not raw_state:
                return None
            state = SceneState.model_validate(raw_state)

        actor = self.runtime.scene_manager._actors.get(scene_id)
        session = actor.group_session if actor else None
        if session is None:
            raw_session = await self.runtime.event_store.load_group_agent_session(scene_id)
            if raw_session:
                from len_bot.cognition.session import GroupAgentSession
                session = GroupAgentSession.model_validate(raw_session)
        return {
            "scene_id": scene_id,
            "version": state.version,
            "activity_level": state.activity_level,
            "consecutive_bot_messages": state.consecutive_bot_messages,
            "participants": state.participants,
            "social_world": session.social_world.model_dump(mode="json") if session else None,
            "self_social_state": session.self_social_state.model_dump(mode="json") if session else None,
            "working_persons": {
                key: value.model_dump(mode="json")
                for key, value in (session.working_persons.items() if session else [])
            },
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
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        params.append(limit)
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
            "SELECT id,scene_id,description,due_at,status,payload,wake_event_type FROM tasks WHERE id=?", (task_id,))
        row = await cursor.fetchone()
        return dict(id=row[0], scene_id=row[1], description=row[2], due_at=row[3], status=row[4],
                    payload=json.loads(row[5]), wake_event_type=row[6]) if row else None

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
        sql = """
            SELECT id, subject, kind, key, value, certainty, scope, status,
                   superseded_by, evidence, human_readable_assertion, created_at, last_confirmed_at
            FROM memories WHERE 1=1
        """
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
        sql += " ORDER BY last_confirmed_at DESC LIMIT ?;"
        params.append(limit)
        cursor = await self.runtime.memory_store._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {"id": r[0], "subject": r[1], "kind": r[2], "key": r[3], "value": r[4],
             "certainty": r[5], "scope": r[6], "status": r[7],
             "superseded_by": r[8], "evidence": json.loads(r[9]) if r[9] else [],
             "human_readable_assertion": r[10], "created_at": r[11], "last_confirmed_at": r[12]}
            for r in rows
        ]

    async def memory_chain(self, memory_id: str) -> list[dict]:
        """Superseded-chain traversal: walk back to the root ancestor, then follow
        superseded_by forward — the full belief evolution, oldest first."""
        store = self.runtime.memory_store
        columns = ("id, subject, kind, key, value, certainty, scope, status, "
                   "superseded_by, evidence, human_readable_assertion, created_at")

        async def fetch(mem_id: str) -> Optional[dict]:
            cursor = await store._db.execute(
                f"SELECT {columns} FROM memories WHERE id = ?;", (mem_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "subject": row[1], "kind": row[2], "key": row[3], "value": row[4],
                "certainty": row[5], "scope": row[6], "status": row[7],
                "superseded_by": row[8], "evidence": json.loads(row[9]) if row[9] else [],
                "human_readable_assertion": row[10], "created_at": row[11],
            }

        # 1. Walk backward (who did this memory supersede?) to find the root
        current = await fetch(memory_id)
        if not current:
            return []
        while True:
            cursor = await store._db.execute(
                "SELECT id FROM memories WHERE superseded_by = ? ORDER BY created_at ASC LIMIT 1;",
                (current["id"],)
            )
            row = await cursor.fetchone()
            if not row:
                break
            ancestor = await fetch(row[0])
            if not ancestor:
                break
            current = ancestor

        # 2. Walk forward via superseded_by from the root
        chain = []
        seen = set()
        node = current
        while node and node["id"] not in seen:
            seen.add(node["id"])
            chain.append(node)
            node = await fetch(node["superseded_by"]) if node["superseded_by"] else None
        return chain

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

    def providers(self) -> dict:
        return self.runtime.provider_registry.snapshot()

    def shadow_would_send(self, limit: int = 100) -> list[dict]:
        return list(self.runtime.shadow_would_send_log)[-limit:][::-1]

    async def list_shadow_annotations(self, scene_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        return await self.runtime.event_store.get_shadow_annotations(scene_id=scene_id, limit=limit)
