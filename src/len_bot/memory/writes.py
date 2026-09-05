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
    if not mp.key or not mp.key.strip():
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
            if row[0] in {"SOCIAL_COGNITION_RECORDED", "REFLECTION_RECORDED", "ACTION_SHADOWED", "TASK_REVIEW"}:
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
            if not original or original[0] in {"SOCIAL_COGNITION_RECORDED", "REFLECTION_RECORDED", "ACTION_SHADOWED", "TASK_REVIEW"}:
                raise ValueError("Episode evidence must resolve to original observations in this scene")
        if not sources:
            raise ValueError("Episode has no source evidence")
        raw_evidence.extend(sources)
    mp.evidence = list(dict.fromkeys(raw_evidence))


async def commit_memory_proposal_core(
    db: aiosqlite.Connection,
    mp: MemoryProposal,
    scene_id: str,
    now: Optional[float] = None
) -> MemoryItem:
    """Executes semantic slot conflict resolution and stores memory via pure SQL.
    
    Does NOT call commit(), enabling use inside any enclosing transaction.
    Always forces mp.scope = scene_id.
    """
    mp.scope = scene_id
    if now is None:
        now = time.time()

    kind_val = mp.kind.value if isinstance(mp.kind, MemoryKind) else str(mp.kind)

    cursor = await db.execute("""
        SELECT id, subject, kind, key, value, temporal, certainty, scope, evidence, status, human_readable_assertion, created_at, last_confirmed_at
        FROM memories
        WHERE subject = ? AND kind = ? AND key = ? AND scope = ? AND status = 'active'
        ORDER BY created_at DESC LIMIT 1;
    """, (mp.subject, kind_val, mp.key, scene_id))
    row = await cursor.fetchone()

    if row:
        old_id, old_subj, old_kind, old_key, old_val, old_temp, old_cert, old_scope, old_ev_json, old_stat, old_assert, old_cat, old_lcat = row
        old_evidence = json.loads(old_ev_json) if old_ev_json else []
        if old_val == mp.value:
            # Value unchanged: confirm and merge evidence
            merged_evidence = list(dict.fromkeys(old_evidence + mp.evidence))
            confirmed_at = now if set(mp.evidence) - set(old_evidence) else old_lcat
            await db.execute("""
                UPDATE memories
                SET last_confirmed_at = ?, evidence = ?, human_readable_assertion = ?
                WHERE id = ?;
            """, (confirmed_at, json.dumps(merged_evidence, ensure_ascii=False), mp.human_readable_assertion, old_id))
            return MemoryItem(
                id=old_id,
                subject=old_subj,
                kind=old_kind,
                key=old_key,
                value=old_val,
                temporal=old_temp,
                certainty=MemoryCertainty(old_cert),
                scope=old_scope,
                evidence=merged_evidence,
                status=MemoryStatus.ACTIVE,
                human_readable_assertion=mp.human_readable_assertion,
                created_at=old_cat,
                last_confirmed_at=confirmed_at
            )
        else:
            # Value conflict: supersede previous memory and create new active record
            new_mem_id = f"mem_{uuid.uuid4().hex[:10]}"
            await db.execute("""
                UPDATE memories SET status = 'superseded', superseded_by = ? WHERE id = ?;
            """, (new_mem_id, old_id))
            evidence_json = json.dumps(mp.evidence, ensure_ascii=False)
            await db.execute("""
                INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope, evidence, status, human_readable_assertion, created_at, last_confirmed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?);
            """, (
                new_mem_id, mp.subject, kind_val, mp.key, mp.value, mp.temporal,
                mp.certainty.value if hasattr(mp.certainty, "value") else str(mp.certainty),
                scene_id, evidence_json, mp.human_readable_assertion, now, now
            ))
            return MemoryItem(
                id=new_mem_id,
                subject=mp.subject,
                kind=mp.kind,
                key=mp.key,
                value=mp.value,
                temporal=mp.temporal,
                certainty=mp.certainty,
                scope=scene_id,
                evidence=mp.evidence,
                status=MemoryStatus.ACTIVE,
                human_readable_assertion=mp.human_readable_assertion,
                created_at=now,
                last_confirmed_at=now
            )
    else:
        # No prior slot conflict: insert new active memory
        new_mem_id = f"mem_{uuid.uuid4().hex[:10]}"
        evidence_json = json.dumps(mp.evidence, ensure_ascii=False)
        await db.execute("""
            INSERT INTO memories (id, subject, kind, key, value, temporal, certainty, scope, evidence, status, human_readable_assertion, created_at, last_confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?);
        """, (
            new_mem_id, mp.subject, kind_val, mp.key, mp.value, mp.temporal,
            mp.certainty.value if hasattr(mp.certainty, "value") else str(mp.certainty),
            scene_id, evidence_json, mp.human_readable_assertion, now, now
        ))
        return MemoryItem(
            id=new_mem_id,
            subject=mp.subject,
            kind=mp.kind,
            key=mp.key,
            value=mp.value,
            temporal=mp.temporal,
            certainty=mp.certainty,
            scope=scene_id,
            evidence=mp.evidence,
            status=MemoryStatus.ACTIVE,
            human_readable_assertion=mp.human_readable_assertion,
            created_at=now,
            last_confirmed_at=now
        )
