"""Unified memory proposal validation and transactional resolution (ADR-0025, §13)."""

import json
import logging
import time
import uuid
from typing import Optional
import aiosqlite

from len_bot.memory.models import MemoryProposal, MemoryItem, MemoryKind, MemoryCertainty, MemoryStatus

logger = logging.getLogger(__name__)


async def validate_memory_proposal(
    db: aiosqlite.Connection,
    mp: MemoryProposal,
    scene_id: str
) -> None:
    """Validates memory proposal constraints authoritatively.
    
    1. Scope is strictly clamped to scene_id (models cannot escape scope).
    2. Key must be non-empty.
    3. Kind must be a valid MemoryKind enum member.
    4. Evidence must be non-empty and deduplicated.
    5. Every evidence event ID must exist in this scene's events or episodes.
    """
    # 1. Authoritative scope clamping
    mp.scope = scene_id

    # 2. Key validation
    if mp.operation != "refute" and (not mp.key.strip() or not mp.subject.strip() or not mp.value.strip()):
        raise ValueError("Memory proposal key cannot be empty")

    # 3. Kind validation
    if not isinstance(mp.kind, MemoryKind):
        try:
            mp.kind = MemoryKind(mp.kind)
        except ValueError as e:
            raise ValueError(f"Invalid memory kind '{mp.kind}': must be one of {[k.value for k in MemoryKind]}") from e

    # 4. Evidence non-empty & deduplicated
    if not mp.evidence:
        raise ValueError("Memory proposal must contain at least one evidence event ID")
    mp.evidence = list(dict.fromkeys(mp.evidence))

    # 5. An episode is an index into original observations, not independent evidence.
    raw_evidence = []
    for ev_id in mp.evidence:
        cursor = await db.execute("SELECT event_type FROM events WHERE id=? AND scene_id=?", (ev_id, scene_id))
        row = await cursor.fetchone()
        if row:
            if row[0] == "TOOL_OBSERVATION_RECORDED":
                detail = await (await db.execute("SELECT payload FROM events WHERE id=? AND scene_id=?", (ev_id, scene_id))).fetchone()
                if not json.loads(detail[0]).get("independent_evidence"):
                    raise ValueError("Derived tool output is not independent memory evidence")
            if row[0] in {"SOCIAL_COGNITION_RECORDED", "REFLECTION_RECORDED", "ACTION_SHADOWED", "TASK_REVIEW", "AGENT_JOB_FINISHED", "AGENT_JOB_PROGRESS", "AGENT_JOB_CHECKPOINT", "AGENT_JOB_CONTROL", "OPERATOR_ACTION"}:
                raise ValueError("Model output is not independent memory evidence")
            raw_evidence.append(ev_id)
            continue
        cursor = await db.execute("SELECT source_event_ids FROM episodes WHERE id=? AND scene_id=?", (ev_id, scene_id))
        episode = await cursor.fetchone()
        if not episode:
            raise ValueError(
                f"Evidence integrity check failed: Memory evidence '{ev_id}' does not belong to scene '{scene_id}' (provenance check failed)"
            )
        sources = json.loads(episode[0])
        for source in sources:
            cursor = await db.execute("SELECT event_type FROM events WHERE id=? AND scene_id=?", (source, scene_id))
            original = await cursor.fetchone()
            if not original or original[0] in {"SOCIAL_COGNITION_RECORDED", "REFLECTION_RECORDED", "ACTION_SHADOWED", "TASK_REVIEW", "AGENT_JOB_FINISHED", "AGENT_JOB_PROGRESS", "AGENT_JOB_CHECKPOINT", "AGENT_JOB_CONTROL", "OPERATOR_ACTION"}:
                raise ValueError("Episode evidence must resolve to original observations in this scene")
            if original[0] == "TOOL_OBSERVATION_RECORDED":
                detail = await (await db.execute("SELECT payload FROM events WHERE id=? AND scene_id=?", (source, scene_id))).fetchone()
                if not json.loads(detail[0]).get("independent_evidence"):
                    raise ValueError("Episode cannot launder derived tool output into evidence")
        if not sources:
            raise ValueError("Episode has no source evidence")
        raw_evidence.extend(sources)
    mp.evidence = list(dict.fromkeys(raw_evidence))
    if mp.operation != "refute":
        originals = await (await db.execute(
            f"SELECT event_type,actor_id FROM events WHERE scene_id=? AND id IN ({','.join('?' for _ in raw_evidence)})",
            [scene_id, *raw_evidence],
        )).fetchall()
        if all(kind in {"MESSAGE_SENT", "MESSAGE_SEND_FAILED"} and actor != mp.subject for kind, actor in originals):
            raise ValueError("Bot speech is not independent evidence about another person")


async def commit_memory_proposal_core(
    db: aiosqlite.Connection,
    mp: MemoryProposal,
    scene_id: str,
    now: Optional[float] = None,
) -> MemoryItem:
    """Apply one validated change inside the caller's transaction; never commit here."""
    mp.scope = scene_id
    now = time.time() if now is None else now

    async def load(memory_id: str) -> MemoryItem:
        cursor = await db.execute("SELECT * FROM memories WHERE id=? AND scope=?", (memory_id, scene_id))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("Memory revision target does not belong to this scene")
        data = dict(zip((column[0] for column in cursor.description), row))
        data["evidence"] = json.loads(data["evidence"])
        data["revision_evidence"] = json.loads(data["revision_evidence"])
        item = MemoryItem.model_validate(data)
        if item.status != MemoryStatus.ACTIVE:
            raise ValueError("Memory revision conflict: target is no longer active")
        return item

    async def retire(item: MemoryItem, status: MemoryStatus, replacement_id: str | None):
        cursor = await db.execute(
            """UPDATE memories SET status=?, superseded_by=?, revision_reason=?, revision_evidence=?
               WHERE id=? AND scope=? AND status='active'""",
            (status.value, replacement_id, mp.reason or "后续认识替代同一语义槽位",
             json.dumps(mp.evidence, ensure_ascii=False), item.id, scene_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Memory revision conflict: target changed")

    targets = [await load(memory_id) for memory_id in mp.target_memory_ids]
    if mp.operation == "refute":
        old = targets[0]
        await retire(old, MemoryStatus.REFUTED, None)
        return old.model_copy(update={"status": MemoryStatus.REFUTED,
                                     "revision_reason": mp.reason, "revision_evidence": mp.evidence})

    if any(item.subject != mp.subject or item.kind != mp.kind for item in targets):
        raise ValueError("Supersede must preserve subject and kind; refute and create to correct a person")

    cursor = await db.execute(
        """SELECT id FROM memories WHERE scope=? AND subject=? AND kind=? AND key=?
           AND status='active' ORDER BY created_at DESC LIMIT 1""",
        (scene_id, mp.subject, mp.kind.value, mp.key),
    )
    slot = await cursor.fetchone()
    if mp.operation == "supersede":
        if slot and slot[0] not in mp.target_memory_ids:
            raise ValueError("Replacement slot is occupied by a memory not included in targets")
    elif slot:
        old = await load(slot[0])
        if old.value == mp.value:
            evidence = list(dict.fromkeys(old.evidence + mp.evidence))
            confirmed_at = now if set(mp.evidence) - set(old.evidence) else old.last_confirmed_at
            await db.execute(
                "UPDATE memories SET evidence=?,last_confirmed_at=? WHERE id=? AND scope=? AND status='active'",
                (json.dumps(evidence, ensure_ascii=False), confirmed_at, old.id, scene_id),
            )
            return old.model_copy(update={"evidence": evidence, "last_confirmed_at": confirmed_at})
        targets = [old]

    item = MemoryItem(
        subject=mp.subject, kind=mp.kind, key=mp.key, value=mp.value,
        temporal=mp.temporal, certainty=mp.certainty, scope=scene_id, evidence=mp.evidence,
        human_readable_assertion=mp.human_readable_assertion or mp.value,
        created_at=now, last_confirmed_at=now,
    )
    for old in targets:
        await retire(old, MemoryStatus.SUPERSEDED, item.id)
    await db.execute(
        """INSERT INTO memories
           (id,subject,kind,key,value,temporal,certainty,scope,evidence,status,
            human_readable_assertion,created_at,last_confirmed_at)
           VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?)""",
        (item.id, item.subject, item.kind.value, item.key, item.value, item.temporal,
         item.certainty.value, scene_id, json.dumps(item.evidence, ensure_ascii=False),
         item.human_readable_assertion, now, now),
    )
    return item
