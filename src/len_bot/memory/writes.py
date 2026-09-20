"""Validate original evidence and apply ledger revisions in the caller's transaction."""

import json
import time

import aiosqlite

from len_bot.actions.models import receipt_delivery_status
from len_bot.events.models import Event, EventType, human_event_uid
from len_bot.memory.models import MemoryBasis, MemoryItem, MemoryKind, MemoryProposal, MemoryStatus
from len_bot.memory.store import MEMORY_COLUMNS, memory_from_row


_EXTERNAL_EVENTS = {EventType.USER_JOINED, EventType.LIVE_STARTED, EventType.LIVE_ENDED}


async def _load_target(db: aiosqlite.Connection, memory_id: str, scene_id: str) -> MemoryItem:
    row = await (await db.execute(
        f"SELECT {','.join(MEMORY_COLUMNS)} FROM memories WHERE id=? AND scope=?", (memory_id, scene_id),
    )).fetchone()
    if row is None:
        raise ValueError("Memory revision target does not belong to this scene")
    item = memory_from_row(row)
    if item.status != MemoryStatus.ACTIVE:
        raise ValueError("Memory revision conflict: target is no longer active")
    return item


async def validate_memory_proposal(
    db: aiosqlite.Connection,
    mp: MemoryProposal,
    scene_id: str,
    through_rowid: int | None = None,
    *,
    bot_actor_id: str,
) -> None:
    """Authoritatively clamp scope, subject and source provenance.

    Evidence is never expanded from summaries or other memories. A sent Bot
    message can accompany a relationship observation but cannot prove a fact.
    Human reports remain reports; this validator does not claim to verify truth.
    """
    if not scene_id or not bot_actor_id:
        raise ValueError("Memory validation requires scene and Bot identity")
    mp.scope = scene_id
    targets = [await _load_target(db, memory_id, scene_id) for memory_id in mp.target_memory_ids]
    if mp.operation == "refute":
        target = targets[0]
        if mp.subject and mp.subject != target.subject:
            raise ValueError("Refutation subject does not match its target")
        mp.subject, mp.kind, mp.basis = target.subject, target.kind, target.basis
    if any(item.subject != mp.subject or item.kind != mp.kind for item in targets):
        raise ValueError("A revision preserves subject and kind; refute and create to correct attribution")
    # A retraction validates the old target and its new correction evidence;
    # it must not require proving the assertion it is removing.
    if mp.operation != "refute" and mp.subject == bot_actor_id:
        raise ValueError("Bot capabilities and real-world experiences belong to runtime facts, not social memory")
    if mp.operation != "refute" and mp.subject != scene_id:
        sql = """SELECT 1 FROM events WHERE scene_id=? AND actor_id=?
            AND event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED','USER_JOINED')
            AND COALESCE(json_extract(metadata,'$.simulated'),0)=0
            AND COALESCE(json_extract(payload,'$.origin_mode'),'')!='simulated'"""
        params: list = [scene_id, mp.subject]
        if through_rowid is not None:
            sql += " AND rowid<=?"
            params.append(through_rowid)
        if await (await db.execute(sql + " LIMIT 1", params)).fetchone() is None:
            raise ValueError("Memory subject is not an observed participant in this scene")
    if mp.operation != "refute" and mp.kind == MemoryKind.GROUP_NORM and mp.subject != scene_id:
        raise ValueError("Group norms use the scene ID as subject")

    originals = []
    operator_refutation = False
    for event_id in mp.evidence:
        row = await (await db.execute(
            "SELECT rowid,event_type,actor_id,timestamp,payload,metadata FROM events WHERE id=? AND scene_id=?", (event_id, scene_id),
        )).fetchone()
        if row is None or (through_rowid is not None and row[0] > through_rowid):
            raise ValueError("Memory evidence is outside this scene or the actual read cutoff")
        _rowid, event_type, actor_id, timestamp, raw_payload, raw_metadata = row
        event = Event(id=event_id, event_type=event_type, actor_id=actor_id, scene_id=scene_id,
                      timestamp=timestamp, payload=json.loads(raw_payload), metadata=json.loads(raw_metadata))
        event_type, actor_id = event.event_type, event.actor_id
        if event.metadata.get('simulated') or event.payload.get('origin_mode') == 'simulated':
            raise ValueError("Simulated events are not original memory evidence")
        human = human_event_uid(event) is not None and event.actor_id != bot_actor_id
        if human or event_type in _EXTERNAL_EVENTS:
            originals.append((event_type, actor_id, human))
        elif event_type == "TOOL_OBSERVATION_RECORDED":
            if event.payload.get("independent_evidence") is not True:
                raise ValueError("Derived tool output is not independent memory evidence")
            originals.append((event_type, actor_id, False))
        elif event_type == "MESSAGE_SENT" and actor_id == bot_actor_id and mp.kind == MemoryKind.RELATIONSHIP:
            if (event.payload.get('origin_mode') != 'live'
                    or receipt_delivery_status(event.event_type, event.payload, event.metadata) != 'sent'):
                raise ValueError("A relationship context requires a real delivered Bot message")
            originals.append((event_type, actor_id, False))
        elif event_type == "OPERATOR_ACTION" and mp.operation == "refute":
            payload = event.payload
            operator = payload.get("operator")
            if (not isinstance(operator, str) or not operator or actor_id != f"operator:{operator}"
                    or payload.get("operation") != "memory_refute"
                    or payload.get("target_memory_id") != mp.target_memory_ids[0]
                    or payload.get("reason") != mp.reason):
                raise ValueError("Operator evidence must explicitly refute this memory with the same reason")
            originals.append((event_type, actor_id, False))
            operator_refutation = True
        else:
            raise ValueError("Model output, Bot assertions, and summaries are not independent memory evidence")
    human_sources = [actor_id for _event_type, actor_id, human in originals if human]
    if (mp.kind in {MemoryKind.ADDRESS, MemoryKind.PREFERENCE, MemoryKind.RELATIONSHIP, MemoryKind.GROUP_NORM}
            and not human_sources and not operator_refutation):
        raise ValueError("Social preferences and relationship observations require human original speech")
    if mp.operation != "refute" and mp.basis == MemoryBasis.REPORTED and not human_sources:
        raise ValueError("A reported belief requires an original human report")
    if (mp.operation != "refute" and mp.basis == MemoryBasis.REPORTED and mp.kind in {MemoryKind.ADDRESS, MemoryKind.PREFERENCE}
            and mp.subject != scene_id and mp.subject not in human_sources):
        raise ValueError("An explicit personal preference must include that person's own statement")
    if not originals:
        raise ValueError("Memory requires independent original evidence")


async def commit_memory_proposal_core(
    db: aiosqlite.Connection,
    mp: MemoryProposal,
    scene_id: str,
    now: float | None = None,
    *,
    revision_event_id: str | None = None,
) -> MemoryItem:
    """Apply a previously validated change without committing the transaction."""
    if mp.scope != scene_id:
        raise ValueError("Memory proposal must be validated for this scene before commit")
    now = time.time() if now is None else now
    targets = [await _load_target(db, memory_id, scene_id) for memory_id in mp.target_memory_ids]
    if any(item.subject != mp.subject or item.kind != mp.kind for item in targets):
        raise ValueError("A revision preserves subject and kind; refute and create to correct attribution")

    async def retire(item: MemoryItem, status: MemoryStatus, replacement: str | None) -> MemoryItem:
        cursor = await db.execute(
            """UPDATE memories SET status=?,superseded_by=?,revision_reason=?,revision_evidence=?,
                 revision_event_id=?,revision=revision+1 WHERE id=? AND scope=? AND status='active' AND revision=?""",
            (status.value, replacement, mp.reason, json.dumps(mp.evidence, ensure_ascii=False),
             revision_event_id, item.id, scene_id, item.revision),
        )
        if cursor.rowcount != 1:
            raise ValueError("Memory revision conflict: target changed")
        return item.model_copy(update={
            "status": status, "superseded_by": replacement, "revision_reason": mp.reason,
            "revision_evidence": list(mp.evidence), "revision_event_id": revision_event_id,
            "revision": item.revision + 1,
        })

    if mp.operation == "refute":
        return await retire(targets[0], MemoryStatus.REFUTED, None)
    if mp.expires_at is not None and mp.expires_at <= now:
        raise ValueError("An already expired preference or belief cannot become active")
    item = MemoryItem(
        scope=scene_id, subject=mp.subject, kind=mp.kind, statement=mp.statement,
        basis=mp.basis, evidence=list(mp.evidence), expires_at=mp.expires_at, created_at=now,
        revision=max((old.revision for old in targets), default=0) + 1,
        created_event_id=revision_event_id, revision_event_id=revision_event_id,
        supersedes_ids=[old.id for old in targets], revision_reason=mp.reason,
        revision_evidence=list(mp.evidence) if targets else [],
    )
    for old in targets:
        await retire(old, MemoryStatus.SUPERSEDED, item.id)
    data = item.model_dump(mode="json")
    for key in ("evidence", "supersedes_ids", "revision_evidence"):
        data[key] = json.dumps(data[key], ensure_ascii=False)
    await db.execute(
        f"INSERT INTO memories ({','.join(MEMORY_COLUMNS)}) VALUES ({','.join('?' for _ in MEMORY_COLUMNS)})",
        [data[key] for key in MEMORY_COLUMNS],
    )
    return item
