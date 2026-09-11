"""Incremental, source-located history caches and Actor-owned atomic commits."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import Field

from len_bot.cognition.projection import estimate_tokens, project_onebot_text
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryModel
from len_bot.memory.writes import commit_memory_proposal_core, validate_memory_proposal


class HistoryConflictError(ValueError):
    """Coverage or knowledge changed before an atomic maintenance commit."""


class HistoryBatch(MemoryModel):
    id: str
    scene_id: str
    start_rowid: int
    start_offset: int
    end_rowid: int
    end_offset: int
    source_event_ids: list[str]
    complete_event_ids: list[str]
    estimated_tokens: int
    segments: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    generation_version: str = "history-v1"


def history_source_text(event: Event) -> str:
    """Stable textual projection; no image interpretation or credential payload."""
    data = {"event_id": event.id, "event_type": event.event_type.value, "actor_id": event.actor_id,
            "timestamp": event.timestamp, "text": project_onebot_text(event.raw_text)}
    if event.payload.get("sender"):
        data["sender"] = {key: value for key, value in event.payload["sender"].items()
                          if key in {"nickname", "card", "user_id"}}
    if event.metadata.get("media"):
        data["media"] = [{key: item[key] for key in ("asset_id", "media_id", "event_id", "kind", "status") if key in item}
                         for item in event.metadata["media"] if isinstance(item, dict)]
        data["media_coverage"] = "references_only_not_interpreted"
    if event.event_type == EventType.MESSAGE_SEND_FAILED:
        data["delivery"] = "not_confirmed"
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


_HISTORY_TYPES = [EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED,
                  EventType.MESSAGE_SENT, EventType.LIVE_STARTED, EventType.LIVE_ENDED,
                  EventType.USER_JOINED, EventType.MESSAGE_SEND_FAILED]


class HistoryStoreMixin:
    async def initialize_history(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS history_origins (
            scene_id TEXT PRIMARY KEY, initial_history_boundary INTEGER NOT NULL)""")
        await self._db.execute("""CREATE TABLE IF NOT EXISTS history_batches (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL,
            start_rowid INTEGER NOT NULL,start_offset INTEGER NOT NULL,
            end_rowid INTEGER NOT NULL,end_offset INTEGER NOT NULL,
            source_event_ids_json TEXT NOT NULL,complete_event_ids_json TEXT NOT NULL,
            estimated_tokens INTEGER NOT NULL,generation_version TEXT NOT NULL,
            status TEXT NOT NULL,summary TEXT,key_event_ids_json TEXT NOT NULL DEFAULT '[]',
            error_type TEXT,created_at REAL NOT NULL,completed_at REAL,
            UNIQUE(scene_id,start_rowid,start_offset,end_rowid,end_offset,generation_version))""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_history_scene ON history_batches(scene_id,end_rowid,end_offset)")

    async def initialize_history_origin(self, scene_id: str, after_rowid: int = 0):
        """Explicit migration boundary, not a claim that earlier history was summarized."""
        async with self._write_lock:
            await self._db.execute("INSERT INTO history_origins VALUES(?,?) ON CONFLICT(scene_id) DO NOTHING",
                                   (scene_id, after_rowid))
            await self._db.commit()

    async def begin_history_batch(self, scene_id: str, *, target_tokens, min_tokens, quiet,
                                  input_budget_tokens: int, estimate_input: Callable[[HistoryBatch], int]):
        if target_tokens < 1 or min_tokens < 1 or min_tokens > target_tokens:
            raise ValueError("Invalid incremental history token limits")
        if input_budget_tokens < 1:
            raise ValueError("History maintenance needs positive input capacity")
        batch_id = uuid.uuid4().hex

        def candidate(parts, tokens):
            return HistoryBatch(id=batch_id, scene_id=scene_id,
                start_rowid=parts[0]['rowid'], start_offset=parts[0]['start_offset'],
                end_rowid=parts[-1]['rowid'], end_offset=parts[-1]['end_offset'],
                source_event_ids=[part['event_id'] for part in parts],
                complete_event_ids=[part['event_id'] for part in parts if part['complete']],
                estimated_tokens=tokens, segments=parts)

        async with self._write_lock:
            unfinished = await (await self._db.execute(
                "SELECT id FROM history_batches WHERE scene_id=? AND status!='completed' LIMIT 1", (scene_id,))).fetchone()
            if unfinished:
                # Failure and process interruption require an explicit retry.
                return None
            last = await (await self._db.execute("""SELECT end_rowid,end_offset FROM history_batches
                WHERE scene_id=? AND status='completed' ORDER BY end_rowid DESC,end_offset DESC LIMIT 1""",
                (scene_id,))).fetchone()
            origin = await (await self._db.execute(
                "SELECT initial_history_boundary FROM history_origins WHERE scene_id=?", (scene_id,))).fetchone()
            boundary = origin[0] if origin else 0
            after_rowid, after_offset = last if last else (boundary, None)
            events = await self.get_events_since(scene_id, after_rowid=max(0, after_rowid - (1 if last else 0)),
                                                  limit=1000, event_types=_HISTORY_TYPES, conversation_only=True)
            segments = []
            used = 0
            full_block = False
            for event in events:
                rowid = event.metadata["_rowid"]
                if event.metadata.get('conversation_excluded'):
                    continue
                text = history_source_text(event)
                start = after_offset if rowid == after_rowid and after_offset is not None else 0
                if start >= len(text):
                    continue

                def segment(end):
                    return {"event_id": event.id, "rowid": rowid, "start_offset": start,
                            "end_offset": end, "total_characters": len(text), "text": text[start:end],
                            "complete": start == 0 and end == len(text)}

                def fits(end):
                    cost = estimate_tokens(text[start:end])
                    return (used + cost <= target_tokens
                            and estimate_input(candidate([*segments, segment(end)], used + cost)) <= input_budget_tokens)

                cost = estimate_tokens(text[start:])
                if segments and not fits(len(text)):
                    full_block = True
                    break
                end = len(text)
                if not fits(end):
                    low, high = start, len(text)
                    while low < high:
                        midpoint = (low + high + 1) // 2
                        if fits(midpoint):
                            low = midpoint
                        else:
                            high = midpoint - 1
                    end = low
                    if end == start:
                        raise ValueError('维护提示、工具定义与最小原文片段超出输入容量，未建立批次')
                    cost = estimate_tokens(text[start:end])
                segments.append(segment(end))
                used += cost
                if end < len(text) or used >= target_tokens:
                    full_block = True
                    break
            if not segments or (not full_block and (not quiet or used < min_tokens)):
                return None
            batch = candidate(segments, used)
            await self._db.execute("""INSERT INTO history_batches
                (id,scene_id,start_rowid,start_offset,end_rowid,end_offset,source_event_ids_json,
                 complete_event_ids_json,estimated_tokens,generation_version,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                (batch.id, scene_id, batch.start_rowid, batch.start_offset, batch.end_rowid, batch.end_offset,
                 json.dumps(batch.source_event_ids), json.dumps(batch.complete_event_ids), used,
                 batch.generation_version, self.clock()))
            await self._db.commit()
            return batch

    async def load_history_batch(self, batch_id: str) -> HistoryBatch:
        cursor = await self._db.execute("SELECT * FROM history_batches WHERE id=?", (batch_id,))
        row = await cursor.fetchone()
        if row is None:
            raise LookupError("History batch does not exist")
        item = self._history_row(dict(zip([column[0] for column in cursor.description], row)))
        batch = HistoryBatch.model_validate({key: item[key] for key in HistoryBatch.model_fields if key in item})
        events = await self.events_by_ids(batch.scene_id, batch.source_event_ids, batch.end_rowid)
        by_id = {event.id: event for event in events}
        if len(by_id) != len(batch.source_event_ids):
            raise ValueError("Original history sources are unavailable")
        segments = []
        for index, event_id in enumerate(batch.source_event_ids):
            event = by_id.get(event_id)
            rowid = event.metadata["_rowid"]
            if event.event_type not in _HISTORY_TYPES:
                raise ValueError("Original history source type changed")
            if rowid < batch.start_rowid or rowid > batch.end_rowid:
                raise ValueError("Original history source is outside its saved range")
            text = history_source_text(event)
            start = batch.start_offset if index == 0 else 0
            end = batch.end_offset if index == len(batch.source_event_ids) - 1 else len(text)
            if start < 0 or end < start or end > len(text):
                raise ValueError("Original history offsets are unavailable")
            segments.append({"event_id": event.id, "rowid": rowid, "start_offset": start,
                "end_offset": end, "total_characters": len(text), "text": text[start:end],
                "complete": start == 0 and end == len(text)})
        if [segment["rowid"] for segment in segments] != sorted(segment["rowid"] for segment in segments):
            raise ValueError("Original history sources are out of order")
        if segments[0]["rowid"] != batch.start_rowid or segments[-1]["rowid"] != batch.end_rowid:
            raise ValueError("Original history range boundaries changed")
        if [segment["event_id"] for segment in segments if segment["complete"]] != batch.complete_event_ids:
            raise ValueError("Original history completion markers changed")
        batch.segments = segments
        return batch

    async def fail_history_batch(self, batch_id: str, error_type: str):
        async with self._write_lock:
            await self._db.execute("UPDATE history_batches SET status='failed',error_type=? WHERE id=? AND status='pending'",
                                   (error_type, batch_id))
            await self._db.commit()

    async def retry_history_batch(self, batch_id: str):
        batch = await self.load_history_batch(batch_id)
        async with self._write_lock:
            cursor = await self._db.execute("UPDATE history_batches SET status='pending',error_type=NULL WHERE id=? AND status IN ('failed','pending')",
                                           (batch_id,))
            if cursor.rowcount != 1:
                raise ValueError("Only unsuccessful history batches can be retried")
            await self._db.commit()
        return batch

    async def commit_history_batch(self, scene_id, batch_id, proposals, summary, key_event_ids,
                                   review_event, expected_revision, scene_state_data, *, bot_actor_id):
        """Only SceneActor calls this; summary, memory and receipt succeed together."""
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("History summary must be nonempty")
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                stored = await (await self._db.execute("""SELECT scene_id,status,end_rowid,
                    source_event_ids_json,complete_event_ids_json FROM history_batches WHERE id=?""", (batch_id,))).fetchone()
                if stored is None or stored[0] != scene_id or stored[1] != "pending":
                    raise HistoryConflictError("History batch is missing, completed or failed")
                cutoff, sources, readable = stored[2], set(json.loads(stored[3])), set(json.loads(stored[4]))
                current = await (await self._db.execute(
                    "SELECT json_extract(state_json,'$.knowledge_revision') FROM scene_sessions WHERE scene_id=?",
                    (scene_id,))).fetchone()
                if (current[0] if current else 0) != expected_revision:
                    raise HistoryConflictError("Knowledge revision changed")
                if not await self.references_belong_to_scene(sources, scene_id, cutoff):
                    raise ValueError("History sources must belong to this scene")
                if not set(key_event_ids).issubset(sources):
                    raise ValueError("Summary locations must belong to this history batch")
                if review_event.scene_id != scene_id or review_event.event_type != EventType.REFLECTION_RECORDED:
                    raise ValueError("Invalid history maintenance receipt")
                for item in review_event.payload.get("review_items", []):
                    if not item.get("source_event_ids") or not set(item["source_event_ids"]).issubset(readable):
                        raise ValueError("Review evidence requires completely read original events")
                committed = []
                for proposal in proposals:
                    if not set(proposal.evidence).issubset(readable):
                        raise ValueError("Memory evidence requires completely read original events")
                    await validate_memory_proposal(self._db, proposal, scene_id, cutoff, bot_actor_id=bot_actor_id)
                    committed.append(await commit_memory_proposal_core(
                        self._db, proposal, scene_id, now=self.clock(), revision_event_id=review_event.id))
                review_event.payload["memory_receipts"] = [
                    await self._memory_receipt(proposal, item) for proposal, item in zip(proposals, committed)]
                rowid = await self._write_scene_event(review_event, scene_state_data,
                    advance_session_observation=bool(review_event.payload.get("review_items")))
                await self._db.execute("""UPDATE history_batches SET status='completed',summary=?,
                    key_event_ids_json=?,completed_at=? WHERE id=?""",
                    (summary.strip(), json.dumps(key_event_ids), self.clock(), batch_id))
                await self._db.commit()
                return committed, rowid
            except BaseException:
                await self._db.rollback()
                raise

    @staticmethod
    def _history_row(item):
        for name in ("source_event_ids", "complete_event_ids", "key_event_ids"):
            item[name] = json.loads(item.pop(name + "_json"))
        return item

    async def list_history_batches(self, scene_id, limit=20, status=None):
        cursor = await self._db.execute("""SELECT * FROM history_batches WHERE scene_id=? AND (? IS NULL OR status=?)
            ORDER BY end_rowid DESC,end_offset DESC LIMIT ?""", (scene_id, status, status, limit))
        names = [column[0] for column in cursor.description]
        return [self._history_row(dict(zip(names, row))) for row in await cursor.fetchall()]

    async def list_history_status(self, scene_id):
        origin = await (await self._db.execute("SELECT initial_history_boundary FROM history_origins WHERE scene_id=?", (scene_id,))).fetchone()
        completed = await self.list_history_batches(scene_id, limit=1, status="completed")
        unsuccessful = await (await self._db.execute("SELECT id,status,error_type,start_rowid,start_offset,end_rowid,end_offset FROM history_batches WHERE scene_id=? AND status!='completed'", (scene_id,))).fetchall()
        return {"initial_history_boundary": origin[0] if origin else 0,
                "last_completed": completed[0] if completed else None,
                "unsuccessful": [{"id": row[0], "status": row[1], "error_type": row[2],
                    "start_rowid": row[3], "start_offset": row[4], "end_rowid": row[5], "end_offset": row[6]}
                    for row in unsuccessful]}
