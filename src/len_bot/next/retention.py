"""Explicit retention of expired operational records, in bounded SQLite batches."""
from __future__ import annotations
import asyncio
from contextlib import nullcontext
import json
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .store import encode
from .memory_jobs import MemoryJobs


class RetentionSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    request_days: int = Field(default=7, ge=1)
    timeline_days: int = Field(default=90, ge=32)
    message_days: dict[str, int] = Field(default_factory=dict)

    @field_validator('message_days')
    @classmethod
    def scene_days(cls, values):
        if any(re.fullmatch(r'(group|private):[1-9][0-9]*', scene) is None or days < 1
               for scene, days in values.items()):
            raise ValueError('message_days requires actual scenes and positive days')
        return values


def expire_request(request: dict, now: float) -> dict:
    return {**{key: value for key, value in request.items() if key not in {'messages','tools','texts','request'}},
            'snapshot_expired_at': now}


class Retention:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.last_result = None
        self.error = None

    def batch(self, *, preview: bool = False) -> dict:
        memory = self.runtime.memory
        path = self.runtime.config.database.with_name(self.runtime.config.database.name + '.memory.sqlite3')
        context = (nullcontext(memory.jobs) if memory is not None else
                   MemoryJobs(path) if path.exists() else nullcontext(None))
        with context as jobs:
            return self._batch(jobs, preview=preview)

    def _batch(self, memory_jobs, *, preview: bool) -> dict:
        config, store = self.runtime.config, self.runtime.store
        settings = config.retention
        if settings is None:
            raise ValueError('当前运行配置未启用保留策略')
        db, now = store.db, store.now()
        cutoff, timeline = now - settings.request_days * 86400, now - settings.timeline_days * 86400
        # Latest native binding/system text is still needed by Chat.restore().
        latest = {row[0] for row in db.execute(
            "SELECT MAX(c.id) FROM model_calls c JOIN turns t ON t.id=c.turn_id WHERE c.role='mind' GROUP BY t.scene")}
        rows = [row for row in db.execute(
            "SELECT c.id,c.request FROM model_calls c LEFT JOIN turns t ON t.id=c.turn_id "
            "WHERE c.ended<? AND (c.turn_id IS NULL OR t.ended IS NOT NULL) "
            "AND json_extract(c.request,'$.snapshot_expired_at') IS NULL "
            "AND c.id NOT IN (SELECT MAX(c2.id) FROM model_calls c2 JOIN turns t2 ON t2.id=c2.turn_id "
            "WHERE c2.role='mind' GROUP BY t2.scene) ORDER BY c.ended LIMIT 100", (cutoff,))]
        auxiliary = {}
        for table in ('audio_calls','expression_embedding_calls','learning_batches','jargon_calls',
                      'sticker_calls','reply_effect_calls'):
            status = " AND status='complete'" if table in {'learning_batches','jargon_calls','sticker_calls','reply_effect_calls'} else ''
            auxiliary[table] = db.execute(
                f"SELECT id,request FROM {table} WHERE ended<? AND request IS NOT NULL "
                "AND json_extract(request,'$.snapshot_expired_at') IS NULL" + status + " ORDER BY ended LIMIT 100",
                (cutoff,)).fetchall()
        task_calls = db.execute(
            "SELECT e.id,e.body FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE e.kind='model_call' AND t.status IN ('done','failed','cancelled') "
            "AND json_extract(e.body,'$.ended')<? AND json_extract(e.body,'$.request.snapshot_expired_at') IS NULL "
            "ORDER BY e.id LIMIT 100", (cutoff,)).fetchall()
        old_turns = db.execute(
            "SELECT id FROM turns WHERE ended<? AND id NOT IN "
            "(SELECT turn_id FROM model_calls WHERE turn_id IS NOT NULL AND id IN (SELECT MAX(id) FROM model_calls UNION "
            "SELECT MAX(c.id) FROM model_calls c JOIN turns t ON t.id=c.turn_id WHERE c.role='mind' GROUP BY t.scene)) "
            "AND NOT EXISTS(SELECT 1 FROM reply_effects r WHERE r.turn_id=turns.id) "
            "AND NOT EXISTS(SELECT 1 FROM proactive_wakes p WHERE p.turn_id=turns.id) "
            "AND NOT EXISTS(SELECT 1 FROM expression_embedding_calls e WHERE e.turn_id=turns.id) "
            "ORDER BY ended LIMIT 100", (timeline,)).fetchall()
        old_plugin_calls = db.execute(
            'SELECT id FROM model_calls WHERE plugin IS NOT NULL AND ended<? ORDER BY ended LIMIT 100',
            (timeline,)).fetchall()
        selected_messages = []
        selected_notices = []
        for scene, days in settings.message_days.items():
            threshold = now - days * 86400
            session = db.execute('SELECT last_message_seq FROM mind_sessions WHERE scene=?',(scene,)).fetchone()
            if session is None:
                continue
            through = session[0]
            # Preserve unprocessed input of active learning/ingest pipelines.
            for table in ('learning_state','jargon_state'):
                cursor = db.execute(f'SELECT after_seq FROM {table} WHERE scene=?',(scene,)).fetchone()
                if cursor is not None:
                    through = min(through, cursor[0])
            if memory_jobs is not None:
                jobs = memory_jobs.db
                cursor = jobs.execute('SELECT after_seq FROM memory_cursors WHERE scene=?',(scene,)).fetchone()
                if cursor is not None:
                    through = min(through, cursor[0])
                pending = jobs.execute("SELECT MIN(first_seq) FROM memory_jobs WHERE scene=? AND status!='complete'",(scene,)).fetchone()[0]
                if pending is not None:
                    through = min(through, pending - 1)
            active = db.execute(
                'SELECT MIN(created) FROM mind_entries WHERE scene=? AND seq>'
                'COALESCE((SELECT compact_through FROM mind_sessions WHERE scene=?),0)', (scene,scene)).fetchone()[0]
            if active is not None:
                # The native context contains text, not an invented per-message membership map.
                # Keep scene originals while it is active, including explicitly imported history.
                continue
            selected_messages.extend(db.execute(
                "SELECT m.seq,m.scene,m.platform_id FROM messages m WHERE m.scene=? AND m.seq<=? "
                "AND COALESCE(m.received_at,json_extract(m.body,'$.time'))<? "
                "AND m.seq<(SELECT MAX(seq) FROM messages WHERE scene=?) "
                "AND NOT EXISTS(SELECT 1 FROM expressions e,json_each(e.sources) s WHERE e.scene=m.scene AND s.value=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM jargon j,json_each(j.sample_seqs) s WHERE j.scene=m.scene AND s.value=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM sticker_candidates c WHERE c.source_message_seq=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM sticker_calls c WHERE c.source_message_seq=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM reply_effects r,json_each(r.message_seqs) s WHERE r.scene=m.scene AND s.value=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM reply_effects r,json_each(r.observed_seqs) s WHERE r.scene=m.scene AND s.value=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM media original JOIN message_media link ON link.media_id=original.id "
                "WHERE original.source_message_seq=m.seq AND link.message_seq!=m.seq) "
                "AND NOT EXISTS(SELECT 1 FROM audio_cache a WHERE a.scene=m.scene AND a.platform_id=m.platform_id "
                "AND (a.status IN ('queued','running') OR (a.transcript IS NOT NULL AND a.announced_at IS NULL))) "
                "ORDER BY m.seq LIMIT 100",
                (scene,through,threshold,scene)).fetchall())
            selected_notices.extend(db.execute(
                "SELECT id FROM notices WHERE scene=? AND received_at<? AND id<(SELECT MAX(id) FROM notices) "
                "AND (platform_id IS NULL OR NOT EXISTS(SELECT 1 FROM messages m WHERE m.scene=notices.scene "
                "AND m.platform_id=notices.platform_id)) ORDER BY id LIMIT 100", (scene,threshold)).fetchall())
        memory_calls, extraction_jobs = {}, []
        if memory_jobs is not None:
            for table in ('memory_summary_runs', 'memory_embedding_calls'):
                status = " AND status='complete'" if table == 'memory_summary_runs' else ''
                memory_calls[table] = memory_jobs.db.execute(
                    f"SELECT id,request FROM {table} WHERE ended<? "
                    "AND json_extract(request,'$.snapshot_expired_at') IS NULL" + status + " ORDER BY ended LIMIT 100",
                    (cutoff,)).fetchall()
            extraction_jobs = memory_jobs.db.execute(
                "SELECT j.id,j.details FROM memory_jobs j WHERE j.status='complete' AND j.ended<? "
                "AND EXISTS(SELECT 1 FROM json_each(j.details,'$.calls') c "
                "WHERE json_extract(c.value,'$.request.snapshot_expired_at') IS NULL) ORDER BY j.id LIMIT 100",
                (cutoff,)).fetchall()
        result = {'preview': preview, 'at': now, 'model_snapshots': len(rows),
                  'auxiliary_snapshots': sum(map(len,auxiliary.values())), 'task_snapshots':len(task_calls),
                  'turns':len(old_turns), 'plugin_calls':len(old_plugin_calls),
                  'messages':len(selected_messages), 'notices':len(selected_notices),
                  'protected_latest_mind_requests':len(latest),
                  'memory_snapshots':sum(map(len,memory_calls.values())), 'extraction_jobs':len(extraction_jobs),
                  'scope':'本批数量；原话仅在无活动上下文时清理；保留未处理输入、显式素材来源和仍被引用的轮次。不等于遗忘记忆或删除备份。'}
        if preview:
            return result
        with db:
            for id, raw in rows:
                db.execute('UPDATE model_calls SET request=?,response=NULL WHERE id=?',
                           (encode(expire_request(json.loads(raw),now)),id))
            for table, calls in auxiliary.items():
                for id, raw in calls:
                    response = '' if table == 'expression_embedding_calls' else ',response=NULL'
                    db.execute(f'UPDATE {table} SET request=?{response} WHERE id=?',
                               (encode(expire_request(json.loads(raw),now)),id))
            for id, raw in task_calls:
                body=json.loads(raw);body['request']=expire_request(body['request'],now)
                body['response']['error_body']=None
                db.execute('UPDATE task_events SET body=? WHERE id=?',(encode(body),id))
            for (id,) in old_turns:
                db.execute('DELETE FROM model_calls WHERE turn_id=?',(id,))
                db.execute('DELETE FROM turns WHERE id=?',(id,))
            for (id,) in old_plugin_calls:
                db.execute('DELETE FROM model_calls WHERE id=?', (id,))
            for seq, scene, platform_id in selected_messages:
                db.execute('DELETE FROM image_cache WHERE scene=? AND platform_id=?',(scene,platform_id))
                db.execute('DELETE FROM audio_cache WHERE scene=? AND platform_id=?',(scene,platform_id))
                db.execute('DELETE FROM message_media WHERE message_seq=?',(seq,))
                # Only unreferenced originals go away. Shared/adopted images retain their source message.
                db.execute('DELETE FROM media WHERE source_message_seq=? AND id NOT IN (SELECT media_id FROM message_media) '
                           'AND id NOT IN (SELECT media_id FROM sticker_candidates WHERE media_id IS NOT NULL)',(seq,))
                db.execute('DELETE FROM message_search WHERE rowid=?',(seq,))
                db.execute('DELETE FROM messages WHERE seq=?',(seq,))
            for (id,) in selected_notices:
                db.execute('DELETE FROM notices WHERE id=?',(id,))
        if memory_jobs is not None:
            with memory_jobs.db:
                for table, calls in memory_calls.items():
                    for id, raw in calls:
                        response = '' if table == 'memory_embedding_calls' else ',response=NULL'
                        memory_jobs.db.execute(f'UPDATE {table} SET request=?{response} WHERE id=?',
                                               (encode(expire_request(json.loads(raw),now)),id))
                for id, raw in extraction_jobs:
                    details=json.loads(raw)
                    for call in details['calls']:
                        call['request']=expire_request(call['request'],now)
                        call['response']=None
                    memory_jobs.db.execute('UPDATE memory_jobs SET details=? WHERE id=?',(encode(details),id))
        self.last_result=result
        return result

    async def run(self):
        if self.runtime.config.retention is None:
            return
        while not self.runtime.stopped.is_set():
            try:
                async with self.lock:
                    while True:
                        result=self.batch()
                        self.runtime.notify()
                        if not any(result[key] for key in ('model_snapshots','auxiliary_snapshots','task_snapshots','turns','messages','notices','memory_snapshots','extraction_jobs')):
                            break
                        await asyncio.sleep(0)
            except Exception as error:
                self.error=f'{type(error).__name__}: {error}'
                self.runtime._emit({'type':'retention','status':'failed','error':self.error})
                return
            try:
                await asyncio.wait_for(self.runtime.stopped.wait(),timeout=86400)
            except TimeoutError:
                pass
