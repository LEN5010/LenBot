"""Public interests, stored apart from per-scene social beliefs.

Source visibility comes from acquisition facts and the work's verified clean
context, never a scene name or a caller-supplied set of public identifiers.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from len_bot.cognition.jobs import ResultSpan
from len_bot.tools.results import ToolResult


class InterestItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(default_factory=lambda: 'int_' + uuid.uuid4().hex)
    topic: str = Field(min_length=1, max_length=200)
    record_type: Literal['public_fact', 'agent_evaluation', 'research_intent']
    statement: str = Field(min_length=1, max_length=4000)
    source_observation_ids: list[str] = Field(min_length=1, max_length=16)
    evidence_spans: list[ResultSpan] = Field(default_factory=list)
    observed_at: float
    published_at: float | None = None
    valid_until: float | None = None
    visibility: Literal['public'] = 'public'
    revision: int = Field(default=1, ge=1)
    status: Literal['active', 'superseded', 'withdrawn', 'expired'] = 'active'

    @field_validator('source_observation_ids')
    @classmethod
    def unique_sources(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('来源观察不能重复')
        return value


class InterestStore:
    def __init__(self, event_store):
        self.event_store = event_store

    async def initialize(self) -> None:
        db = self.event_store._db
        await db.execute("""CREATE TABLE IF NOT EXISTS public_interests (
            id TEXT PRIMARY KEY, topic TEXT NOT NULL, record_type TEXT NOT NULL,
            statement TEXT NOT NULL, source_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
            observed_at REAL NOT NULL, published_at REAL, valid_until REAL,
            visibility TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL)""")
        await db.commit()

    async def public_observation_ids(self, observation_ids: list[str]) -> set[str]:
        async def public(ident, visiting):
            if ident in visiting:
                return False
            row = await (await self.event_store._db.execute('SELECT result_json FROM tool_observations WHERE id=?', (ident,))).fetchone()
            if not row:
                return False
            result = ToolResult.model_validate_json(row[0])
            provenance = result.provenance
            if result.status not in {'ok', 'partial'} or provenance.source_event_ids:
                return False
            if provenance.access == 'anonymous_public':
                return True
            if provenance.access != 'derived' or not provenance.source_result_ids:
                return False
            for parent in provenance.source_result_ids:
                if not await public(parent, visiting | {ident}):
                    return False
            return True
        confirmed = set()
        for ident in observation_ids:
            if await public(ident, set()):
                confirmed.add(ident)
        return confirmed

    async def adopt_in_transaction(self, job, candidates):
        """Called only from the work-result transaction after its revision check."""
        from len_bot.runtime.public_research import verify_public_job
        if not candidates:
            return
        if not await verify_public_job(self.event_store, job):
            raise ValueError('混合或未确认上下文不能直接写公共兴趣；需要独立公共研究')
        if len({item.candidate_id for item in candidates}) != len(candidates):
            raise ValueError('同一工作结果的兴趣候选身份不能重复')
        db, now = self.event_store._db, self.event_store.clock()
        for candidate in candidates:
            ids = list(dict.fromkeys(span.result_id for span in candidate.evidence_spans))
            await self.event_store._validate_evidence_spans(job, candidate.evidence_spans, ids)
            if not set(ids).issubset(job['result_ids']) or set(ids) != await self.public_observation_ids(ids):
                raise ValueError('兴趣来源不是本工作实际取得、读过的匿名公共资料')
            if candidate.valid_until is not None and candidate.valid_until <= now:
                raise ValueError('兴趣候选已经过期')
            ident = candidate.interest_id or f'int_{job["id"]}_{job["revision"]}_{candidate.candidate_id}'
            before = await self.get(ident)
            if candidate.operation == 'create':
                if before is not None:
                    raise ValueError('该候选已被采用，不重复写入')
                revision = 1
            else:
                if before is None or before.revision != candidate.expected_revision or before.status != 'active':
                    raise ValueError('原兴趣已经改变或撤回')
                revision = before.revision + 1
            item = InterestItem(id=ident, topic=candidate.topic, record_type=candidate.record_type,
                statement=candidate.statement, source_observation_ids=ids, evidence_spans=candidate.evidence_spans,
                observed_at=now, published_at=candidate.published_at, valid_until=candidate.valid_until,
                revision=revision, status='withdrawn' if candidate.operation == 'withdraw' else 'active')
            await db.execute("""INSERT INTO public_interests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET topic=excluded.topic,record_type=excluded.record_type,
                statement=excluded.statement,source_json=excluded.source_json,evidence_json=excluded.evidence_json,
                observed_at=excluded.observed_at,published_at=excluded.published_at,valid_until=excluded.valid_until,
                revision=excluded.revision,status=excluded.status""", (item.id, item.topic, item.record_type,
                item.statement, json.dumps(ids), json.dumps([span.model_dump() for span in item.evidence_spans]),
                now, item.published_at, item.valid_until, 'public', revision, item.status))
            await db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                (f'interest:{job["id"]}:{job["revision"]}:{candidate.candidate_id}', 'PUBLIC_INTEREST_CHANGED',
                 job['scene_id'], 'system:public_research', now, json.dumps({'job_id': job['id'],
                    'job_revision': job['revision'], 'operation': candidate.operation, 'reason': candidate.reason,
                    'before': before.model_dump(mode='json') if before else None, 'after': item.model_dump(mode='json')},
                    ensure_ascii=False), '{"conversation_excluded":true}'))

    async def get(self, ident):
        row = await (await self.event_store._db.execute('SELECT * FROM public_interests WHERE id=?', (ident,))).fetchone()
        return self._item(row) if row else None

    @staticmethod
    def _item(row):
        return InterestItem(id=row[0], topic=row[1], record_type=row[2], statement=row[3],
            source_observation_ids=json.loads(row[4]), evidence_spans=json.loads(row[5]),
            observed_at=row[6], published_at=row[7], valid_until=row[8],
            visibility=row[9], revision=row[10], status=row[11])

    async def list_public(self, *, topic: str | None = None, now: float | None = None,
                          limit: int = 20) -> list[InterestItem]:
        now = self.event_store.clock() if now is None else now
        db = self.event_store._db
        rows = await (await db.execute(
            """SELECT id,topic,record_type,statement,source_json,evidence_json,observed_at,published_at,
                      valid_until,visibility,revision,status FROM public_interests
               WHERE status='active' AND visibility='public'
                 AND (valid_until IS NULL OR valid_until>?)
                 AND (? IS NULL OR topic=?)
               ORDER BY observed_at DESC LIMIT ?""",
            (now, topic, topic, limit))).fetchall()
        items = []
        for row in rows:
            # Legacy records without acquisition proof remain readable in the
            # original database, but are not offered as current public facts.
            ids = json.loads(row[4])
            if ids and set(ids) == await self.public_observation_ids(ids):
                items.append(self._item(row))
        return items

    async def withdraw(self, interest_id: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError('撤回公共兴趣必须保留原因')
        db = self.event_store._db
        async with self.event_store._write_lock:
            try:
                before = await self.get(interest_id)
                if before is None or before.status != 'active':
                    raise ValueError('兴趣不存在或已经撤回')
                await db.execute("UPDATE public_interests SET status='withdrawn',revision=revision+1 WHERE id=?", (interest_id,))
                await db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)', ('interest-withdraw:' + uuid.uuid4().hex,
                    'PUBLIC_INTEREST_CHANGED', 'system:heartbeat', 'system:interest_store', self.event_store.clock(),
                    json.dumps({'operation': 'withdraw', 'reason': reason, 'before': before.model_dump(mode='json'),
                        'revision': before.revision + 1}, ensure_ascii=False), '{"conversation_excluded":true}'))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
