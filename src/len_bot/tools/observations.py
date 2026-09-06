"""Scoped, append-only tool resources and their durable event envelopes."""
import json
import uuid

from len_bot.events.models import Event, EventType
from len_bot.tools.results import ToolResult


class ObservationStoreMixin:
    async def initialize_observations(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS tool_observations (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, event_id TEXT NOT NULL,
            tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at REAL NOT NULL)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_tool_observations_scene ON tool_observations(scene_id,created_at)")

    async def save_tool_observation(self, scene_id, tool_name, arguments, result: ToolResult, *, background_work=False):
        result = result.model_copy(update={"result_id": uuid.uuid4().hex, "observation_event_id": uuid.uuid4().hex})
        event = Event(id=result.observation_event_id, event_type=EventType.TOOL_OBSERVATION_RECORDED,
                      scene_id=scene_id, actor_id="system:tools", timestamp=self.clock(), metadata={"background_work": background_work}, payload={
                          "result_id": result.result_id, "tool_name": tool_name,
                          "status": result.status, "sources": [s.model_dump() for s in result.sources],
                          "independent_evidence": result.evidence_kind == "external" and result.status in {"ok", "partial"},
                      })
        async with self._write_lock:
            try:
                await self._db.execute("INSERT INTO tool_observations VALUES(?,?,?,?,?,?,?)", (
                    result.result_id, scene_id, event.id, tool_name,
                    json.dumps(arguments, ensure_ascii=False), result.model_dump_json(), self.clock()))
                await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return result, event

    async def read_tool_observation(self, result_id, allowed_scopes):
        if not allowed_scopes:
            return None
        cursor = await self._db.execute(
            f"SELECT result_json FROM tool_observations WHERE id=? AND scene_id IN ({','.join('?' for _ in allowed_scopes)})",
            [result_id, *allowed_scopes])
        row = await cursor.fetchone()
        return ToolResult.model_validate_json(row[0]) if row else None

    async def project_image_observations(self, scene_id, events, through_rowid):
        """Reuse committed observations for images already present in working context."""
        def image_ids(event):
            media = [*event.metadata.get("media", []),
                     *event.metadata.get("quote_context", {}).get("media", [])]
            return {item["asset_id"] for item in media if item.get("asset_id")}

        projected = [event.model_copy(deep=True) for event in events]
        for event in projected:
            event.metadata.pop("image_observations", None)
        asset_ids = set().union(*(image_ids(event) for event in projected))
        if not asset_ids:
            return projected
        rows = await (await self._db.execute(
            "SELECT json_extract(o.arguments_json,'$.asset_id'),o.arguments_json,o.result_json "
            "FROM tool_observations o JOIN events e ON e.id=o.event_id AND e.scene_id=o.scene_id "
            "JOIN media_assets m ON m.id=json_extract(o.arguments_json,'$.asset_id') "
            "WHERE o.scene_id=? AND m.scope IN (?, 'global-safe') AND e.rowid<=? "
            "AND o.tool_name='inspect_image' AND json_extract(o.result_json,'$.status')='ok' "
            "AND m.id IN (SELECT value FROM json_each(?)) ORDER BY o.rowid DESC",
            (scene_id, scene_id, through_rowid, json.dumps(sorted(asset_ids))),
        )).fetchall()
        observations = {}
        for asset_id, raw_arguments, raw_result in rows:
            if asset_id in observations:
                continue
            result = ToolResult.model_validate_json(raw_result)
            observations[asset_id] = {
                "asset_id": asset_id, "question": json.loads(raw_arguments).get("question", ""),
                "content": result.content, "coverage": result.coverage, "evidence_kind": result.evidence_kind,
                "result_id": result.result_id, "observation_event_id": result.observation_event_id,
            }
        attached = set()
        for item in reversed(projected):
            ids = image_ids(item) - attached
            related = [observations[asset_id] for asset_id in sorted(ids) if asset_id in observations]
            if related:
                item.metadata["image_observations"] = related
                attached.update(record["asset_id"] for record in related)
        return projected

    async def tool_observation_call(self, result_id, scene_id):
        row = await (await self._db.execute(
            "SELECT tool_name,arguments_json FROM tool_observations WHERE id=? AND scene_id=?", (result_id, scene_id))).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    async def list_tool_observations(self, scene_id, limit=100):
        cursor = await self._db.execute("SELECT id,event_id,tool_name,result_json,created_at FROM tool_observations WHERE scene_id=? ORDER BY created_at DESC LIMIT ?",
                                        (scene_id, min(max(1, limit), 500)))
        return [{"id": r[0], "event_id": r[1], "tool_name": r[2],
                 "result": ToolResult.model_validate_json(r[3]).page().model_dump(), "created_at": r[4]}
                for r in await cursor.fetchall()]
