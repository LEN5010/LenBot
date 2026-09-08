"""Images are registered by their source event, never silently promoted across scenes."""
import json
import re
import uuid
from html import unescape

from len_bot.events.models import Event, EventType


PALETTE_UNCHANGED = object()


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
    data = dict(zip(["id", "scope", "source_event_id", "locator", "mime_type", "path", "description", "tags", "enabled", "curated", "created_at", "palette_order"], row, strict=True))
    data["tags"] = json.loads(data["tags"])
    data["enabled"], data["curated"] = bool(data["enabled"]), bool(data["curated"])
    return data


class MediaStoreMixin:
    async def initialize_media(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_event_id TEXT NOT NULL,
            locator TEXT NOT NULL DEFAULT '', mime_type TEXT, path TEXT,
            description TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1, curated INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            palette_order INTEGER CHECK(palette_order >= 0))""")
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

    async def retained_media_paths(self):
        """Operator Reset keeps the files belonging to curated media."""
        return [row[0] for row in await (await self._db.execute(
            "SELECT path FROM media_assets WHERE curated=1 AND path IS NOT NULL")).fetchall()]

    async def list_palette(self, scene_id: str, *, limit: int):
        """The operator's fixed palette, including only this scene and global-safe."""
        rows = await (await self._db.execute("""SELECT * FROM media_assets
            WHERE scope IN (?, 'global-safe') AND curated=1 AND enabled=1 AND palette_order IS NOT NULL
            ORDER BY palette_order, created_at, id LIMIT ?""", (scene_id, limit))).fetchall()
        return [_asset(row) for row in rows]

    async def recent_media_sends(self, scene_id: str, *, bot_actor_id: str, limit: int, through_rowid: int | None = None):
        """Count each asset once per real sent message in the bounded scene window."""
        if not scene_id or not bot_actor_id or type(limit) is not int or limit < 1:
            raise ValueError('Recent media use requires a scene, Bot identity and positive send window')
        if through_rowid is not None and (type(through_rowid) is not int or through_rowid < 0):
            raise ValueError('Recent media use requires a nonnegative read cutoff')
        rows = await (await self._db.execute("""SELECT timestamp,payload FROM events
            WHERE scene_id=? AND actor_id=? AND event_type='MESSAGE_SENT'
              AND COALESCE(json_extract(metadata,'$.simulated'),0)=0
              AND json_extract(payload,'$.origin_mode')='live'
              AND json_extract(payload,'$.delivery_status')='sent'
              AND COALESCE(json_extract(payload,'$.delivery_unknown'),0)=0
              AND (? IS NULL OR rowid<=?)
            ORDER BY rowid DESC LIMIT ?""", (scene_id, bot_actor_id, through_rowid, through_rowid, limit))).fetchall()
        usage = {}
        for index, (timestamp, encoded) in enumerate(rows):
            payload = json.loads(encoded)
            assets = {segment['asset_id'] for segment in payload.get('segments', []) if segment['type'] == 'image'}
            for asset_id in assets:
                record = usage.setdefault(asset_id, {'last_sent_at': timestamp, 'recent_send_count': 0,
                                                     'used_in_last_reply': index == 0})
                record['recent_send_count'] += 1
        return usage

    async def list_media(self, allowed_scopes, *, limit: int, query="", curated_only=False, include_disabled=False):
        if type(limit) is not int or limit < 1:
            raise ValueError('Media search requires a positive integer limit')
        if not allowed_scopes:
            return []
        sql = f"SELECT * FROM media_assets WHERE scope IN ({','.join('?' for _ in allowed_scopes)})"
        params = list(allowed_scopes)
        if curated_only:
            sql += " AND curated=1"
        if not include_disabled:
            sql += " AND enabled=1"
        terms = list(dict.fromkeys(query.split()))
        order = "created_at DESC,id"
        if terms:
            matches = ["(instr(lower(description),lower(?))>0 OR instr(lower(tags_json),lower(?))>0)"
                       for _ in terms]
            term_params = [value for term in terms for value in (term, term)]
            sql += " AND (" + " OR ".join(matches) + ")"
            params += term_params
            order = "(" + "+".join(matches) + ") DESC," + order
            params += term_params
        sql += " ORDER BY " + order + " LIMIT ?"
        params.append(limit)
        return [_asset(row) for row in await (await self._db.execute(sql, params)).fetchall()]

    async def save_media_file(self, asset_id, scope, mime_type, path, *, description=None, tags=None, curated=False):
        event = Event(event_type=EventType.MEDIA_UPDATED, scene_id=scope, actor_id="system:media", timestamp=self.clock(),
            payload={"asset_id": asset_id, "curated": curated})
        async with self._write_lock:
            try:
                if curated:
                    await self._db.execute("""INSERT INTO media_assets(id,scope,source_event_id,mime_type,path,description,tags_json,curated,created_at)
                        VALUES(?,?,?,?,?,?,?,1,?)""", (asset_id, scope, event.id, mime_type, path, description or "", json.dumps(tags or [], ensure_ascii=False), self.clock()))
                else:
                    cursor = await self._db.execute("UPDATE media_assets SET mime_type=?,path=? WHERE id=? AND scope=?",
                        (mime_type, path, asset_id, scope))
                    if cursor.rowcount != 1:
                        raise ValueError("Media source is not in this scene")
                await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scope, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return event

    async def save_generated_media(self, asset_id, scene_id, source_event_id, mime_type, path, description):
        event = Event(event_type=EventType.MEDIA_UPDATED, scene_id=scene_id, actor_id='system:media',
            timestamp=self.clock(), payload={'asset_id': asset_id, 'curated': False, 'source_event_id': source_event_id})
        async with self._write_lock:
            try:
                source = await (await self._db.execute('SELECT 1 FROM events WHERE id=? AND scene_id=?',
                                                       (source_event_id, scene_id))).fetchone()
                if not source:
                    raise ValueError('Generated image source is outside this scene')
                await self._db.execute('''INSERT INTO media_assets
                    (id,scope,source_event_id,mime_type,path,description,tags_json,curated,created_at)
                    VALUES(?,?,?,?,?,?,'[]',0,?)''',
                    (asset_id, scene_id, source_event_id, mime_type, path, description, self.clock()))
                await self._db.execute('INSERT INTO pending_runtime_events VALUES(?,?,?)', (event.id, scene_id, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return event

    async def edit_media(self, asset_id, scope, description, tags, enabled, *, palette_order=PALETTE_UNCHANGED):
        if palette_order is not PALETTE_UNCHANGED and palette_order is not None:
            if isinstance(palette_order, bool) or not isinstance(palette_order, int) or palette_order < 0:
                raise ValueError("Palette order must be a nonnegative integer or null")
        payload = {"asset_id": asset_id, "enabled": enabled, "description": description, "tags": tags}
        if palette_order is not PALETTE_UNCHANGED:
            payload["palette_order"] = palette_order
        event = Event(event_type=EventType.MEDIA_UPDATED, scene_id=scope, actor_id="operator:media", timestamp=self.clock(),
            payload=payload)
        async with self._write_lock:
            try:
                fields = "description=?,tags_json=?,enabled=?"
                params = [description, json.dumps(tags, ensure_ascii=False), int(enabled)]
                if palette_order is not PALETTE_UNCHANGED:
                    fields += ",palette_order=?"
                    params.append(palette_order)
                cursor = await self._db.execute(f"UPDATE media_assets SET {fields} WHERE id=? AND scope=? AND curated=1",
                    (*params, asset_id, scope))
                if cursor.rowcount != 1:
                    raise ValueError("Curated media asset not found in selected scope")
                await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scope, event.model_dump_json()))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return event
