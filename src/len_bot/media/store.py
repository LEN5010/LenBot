"""Images are registered by their source event, never silently promoted across scenes."""
import json
import re
import uuid
from html import unescape

from len_bot.events.models import Event, EventType


def image_locators(event):
    segments = event.payload.get("segments")
    if isinstance(segments, list):
        return [str(segment.get("data", {}).get("url") or segment.get("data", {}).get("file") or "")
                for segment in segments if isinstance(segment, dict) and segment.get("type") == "image"]
    sources = []
    for match in re.finditer(r"\[CQ:image,([^\]]+)\]", event.raw_text):
        fields = dict(pair.split("=", 1) for pair in match.group(1).split(",") if "=" in pair)
        sources.append(unescape(fields.get("url") or fields.get("file") or ""))
    return sources


def _asset(row):
    if row is None:
        return None
    data = dict(zip(["id", "scope", "source_event_id", "locator", "sha256", "mime_type", "path", "description", "tags", "enabled", "curated", "created_at"], row))
    data["tags"] = json.loads(data["tags"])
    data["enabled"], data["curated"] = bool(data["enabled"]), bool(data["curated"])
    return data


class MediaStoreMixin:
    async def initialize_media(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_event_id TEXT NOT NULL,
            locator TEXT NOT NULL DEFAULT '', sha256 TEXT, mime_type TEXT, path TEXT,
            description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1, curated INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_media_assets_scope ON media_assets(scope,created_at)")

    async def register_event_media_in_transaction(self, event):
        if event.event_type not in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED, EventType.HISTORICAL_IMPORT}:
            return
        refs = []
        for index, locator in enumerate(image_locators(event)):
            asset_id = "image_" + uuid.uuid5(uuid.NAMESPACE_URL, f"{event.scene_id}:{event.id}:{index}").hex
            await self._db.execute("""INSERT OR IGNORE INTO media_assets(id,scope,source_event_id,locator,created_at)
                VALUES(?,?,?,?,?)""", (asset_id, event.scene_id, event.id, locator, self.clock()))
            refs.append({"asset_id": asset_id, "type": "image", "source_event_id": event.id})
        if refs:
            event.metadata["media"] = refs

    async def get_media(self, asset_id, allowed_scopes, *, include_disabled=False):
        if not allowed_scopes:
            return None
        row = await (await self._db.execute(
            f"SELECT * FROM media_assets WHERE id=? AND scope IN ({','.join('?' for _ in allowed_scopes)})" + ("" if include_disabled else " AND enabled=1"),
            [asset_id, *allowed_scopes])).fetchone()
        return _asset(row)

    async def list_media(self, allowed_scopes, *, query="", curated_only=False, include_disabled=False, limit=40):
        if not allowed_scopes:
            return []
        sql = f"SELECT * FROM media_assets WHERE scope IN ({','.join('?' for _ in allowed_scopes)})"
        params = list(allowed_scopes)
        if curated_only:
            sql += " AND curated=1"
        if not include_disabled:
            sql += " AND enabled=1"
        if query:
            sql += " AND (instr(lower(description),lower(?))>0 OR instr(lower(tags_json),lower(?))>0)"
            params += [query, query]
        sql += " ORDER BY created_at DESC,id LIMIT ?"
        params.append(min(max(1, limit), 100))
        return [_asset(row) for row in await (await self._db.execute(sql, params)).fetchall()]

    async def save_media_file(self, asset_id, scope, sha256, mime_type, path, *, description=None, tags=None, curated=False):
        event = Event(event_type=EventType.MEDIA_UPDATED, scene_id=scope, actor_id="system:media", timestamp=self.clock(),
            payload={"asset_id": asset_id, "sha256": sha256, "curated": curated})
        async with self._write_lock:
            try:
                if curated:
                    await self._db.execute("""INSERT INTO media_assets(id,scope,source_event_id,sha256,mime_type,path,description,tags_json,curated,created_at)
                        VALUES(?,?,?,?,?,?,?,?,1,?)""", (asset_id, scope, event.id, sha256, mime_type, path, description or "", json.dumps(tags or [], ensure_ascii=False), self.clock()))
                else:
                    cursor = await self._db.execute("UPDATE media_assets SET sha256=?,mime_type=?,path=? WHERE id=? AND scope=?",
                        (sha256, mime_type, path, asset_id, scope))
                    if cursor.rowcount != 1:
                        raise ValueError("Media source is not in this scene")
                await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scope, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return event

    async def edit_media(self, asset_id, scope, description, tags, enabled):
        event = Event(event_type=EventType.MEDIA_UPDATED, scene_id=scope, actor_id="operator:media", timestamp=self.clock(),
            payload={"asset_id": asset_id, "enabled": enabled, "description": description, "tags": tags})
        async with self._write_lock:
            try:
                cursor = await self._db.execute("UPDATE media_assets SET description=?,tags_json=?,enabled=? WHERE id=? AND scope=? AND curated=1",
                    (description, json.dumps(tags, ensure_ascii=False), int(enabled), asset_id, scope))
                if cursor.rowcount != 1:
                    raise ValueError("Curated media asset not found in selected scope")
                await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scope, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return event
