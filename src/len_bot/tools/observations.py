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

    async def save_tool_observation(self, scene_id, tool_name, arguments, result: ToolResult, *, background_work=False, media_files=()):
        result = result.model_copy(update={"result_id": uuid.uuid4().hex, "observation_event_id": uuid.uuid4().hex})
        assets = [('image_'+uuid.uuid4().hex, item) for item in media_files]
        result.attachments = [*result.attachments, *[ident for ident, _ in assets]]
        event = Event(id=result.observation_event_id, event_type=EventType.TOOL_OBSERVATION_RECORDED,
                      scene_id=scene_id, actor_id="system:tools", timestamp=self.clock(), metadata={"background_work": background_work}, payload={
                          "result_id": result.result_id, "tool_name": tool_name,
                          "tool_call_id": result.tool_call_id,
                          "status": result.status, "sources": [s.model_dump() for s in result.sources],
                          **({'error_code': result.error_code, 'error_stage': result.error_stage,
                              'http_status': result.http_status} if result.status in {'error','unsupported'} else {}),
                          "independent_evidence": result.evidence_kind == "external" and result.status in {"ok", "partial"},
                          **({'media': result.attachments} if result.attachments else {}),
                      })
        async with self._write_lock:
            try:
                for ident, item in assets:
                    await self._db.execute('''INSERT INTO media_assets
                        (id,scope,source_event_id,locator,mime_type,path,description,tags_json,curated,created_at)
                        VALUES(?,?,?,?,?,?,?,'[]',0,?)''', (ident, scene_id, event.id, item['locator'],
                        item['mime_type'], item['path'], item['description'], self.clock()))
                for ident in result.attachments:
                    if await self.get_media(ident, [scene_id, 'global-safe']) is None:
                        raise ValueError('Tool image is not available in this scene')
                await self._db.execute("INSERT INTO tool_observations VALUES(?,?,?,?,?,?,?)", (
                    result.result_id, scene_id, event.id, tool_name,
                    json.dumps({} if result.status in {'error','unsupported'} else arguments, ensure_ascii=False),
                    result.model_dump_json(round_trip=True), self.clock()))
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

    async def tool_observation_call(self, result_id, scene_id):
        row = await (await self._db.execute(
            "SELECT tool_name,arguments_json FROM tool_observations WHERE id=? AND scene_id=?", (result_id, scene_id))).fetchone()
        return (row[0], json.loads(row[1])) if row else None
