"""Work detail belongs to tasks; budgets and observations survive revision/restart."""
import json
import uuid

from len_bot.cognition.jobs import JobChanged, JobBudgetExhausted, JobResultRejected, JobResult, ResultPresentation, WorkState
from len_bot.events.models import Event, EventType
from len_bot.scheduler.models import TaskItem
from len_bot.skills.store import SkillStoreMixin


def _decode_job(row):
    if not row:
        return None
    fields = ["id", "scene_id", "revision", "goal", "constraints", "source_event_ids", "result_ids",
              "model_steps", "tool_calls", "elapsed_seconds", "result", "updated_at",
              "work_state", "model_binding", "checkpoint_data", "compression", "skill_versions", "status", "origin_mode", "created_at", "task_payload"]
    data = dict(zip(fields, row))
    for field in ("constraints", "source_event_ids", "result_ids", "result", "work_state", "model_binding", "checkpoint_data", "compression", "skill_versions"):
        data[field] = json.loads(data[field]) if data[field] else None
    native = data.pop("checkpoint_data")
    data["checkpoint"] = {key: native[key] for key in ("goal_revision", "exchange_count", "updated_at")} if native else None
    data["skill_versions"] = data["skill_versions"] or {}
    task_payload = json.loads(data.pop("task_payload"))
    for field in ("work_operation", "requester_qq_uid"):
        data[field] = task_payload[field]
    for field in ('plugin_origin','work_parameters','work_progress'):
        data[field]=task_payload.get(field)
    data['legacy_payload']=task_payload if data['work_operation']!='information' and data['plugin_origin'] is None else None
    # Older work has no distinct request anchor; expose absence rather than infer
    # one from the accumulated evidence or rewrite historical identities.
    data['request_source_event_id'] = task_payload.get('request_source_event_id')
    data['ack_action_id'] = task_payload.get('ack_action_id')
    data['delivery_action_id'] = task_payload.get('delivery_action_id')
    data['delivery_event_id'] = task_payload.get('delivery_event_id')
    data['observation_reads'] = task_payload.get('observation_reads', {})
    data['resume_from'] = task_payload.get('resume_from')
    # Task status describes response/delivery, not whether execution succeeded.
    data["execution_status"] = (data["result"] or {}).get("status") or {
        "pending": "pending", "claimed": "pending", "processing": "running",
        "review_required": "interrupted", "failed": "failed", "cancelled": "cancelled",
    }.get(data["status"], "unknown")
    data["can_resume"] = ((data["status"] in {"review_required", "failed", "result_ready"}
                          and data["execution_status"] in {"failed", "interrupted"})
                         or data['status'] in {'completed','failed','result_ready','review_required'}
                         and data['execution_status']=='partial' and bool((data['result'] or {}).get('unresolved')))
    return data


class JobStoreMixin(SkillStoreMixin):
    def plugin_work(self, job):
        if self.resolve_plugin_work is None:
            if job['work_operation']!='information':raise ValueError('Specialized work requires its registered plugin')
            return None
        return self.resolve_plugin_work(job['plugin_origin'],job['work_operation'])

    async def _new_work_progress(self, scene_id, proposal, previous=None):
        spec=self.plugin_work({'plugin_origin':proposal.plugin_origin,'work_operation':proposal.work_operation})
        if spec is None:return None
        parameters=spec.parameters_model.model_validate(proposal.work_parameters)
        prior=(spec.parameters_model.model_validate(previous['work_parameters']),
            spec.progress_model.model_validate(previous['work_progress'])) if previous else None
        progress=await spec.new_progress(self,scene_id,parameters,prior)
        return spec.progress_model.model_validate(progress).model_dump(mode='json')

    async def initialize_jobs(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS agent_jobs (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, revision INTEGER NOT NULL,
            goal TEXT NOT NULL, constraints_json TEXT NOT NULL, source_event_ids_json TEXT NOT NULL,
            result_ids_json TEXT NOT NULL, model_steps INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL NOT NULL DEFAULT 0,
            result_json TEXT, updated_at REAL NOT NULL,
            work_state_json TEXT, model_binding_json TEXT, checkpoint_json TEXT,
            compression_json TEXT, skill_versions_json TEXT)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_agent_jobs_scene ON agent_jobs(scene_id,updated_at)")
        await self._db.execute("""CREATE TABLE IF NOT EXISTS job_exchanges (
            job_id TEXT NOT NULL, scene_id TEXT NOT NULL, sequence INTEGER NOT NULL, goal_revision INTEGER NOT NULL,
            messages_json TEXT NOT NULL, PRIMARY KEY(job_id,sequence))""")
        await self.initialize_skills()

    async def get_job(self, job_id, scene_id):
        row = await (await self._db.execute("""SELECT j.*,t.status,t.origin_mode,t.created_at,t.payload FROM agent_jobs j
            JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id WHERE j.id=? AND j.scene_id=?""", (job_id, scene_id))).fetchone()
        return _decode_job(row)

    async def list_jobs(self, scene_id=None):
        clause, params = (" WHERE j.scene_id=?", (scene_id,)) if scene_id is not None else ("", ())
        rows = await (await self._db.execute("SELECT j.*,t.status,t.origin_mode,t.created_at,t.payload FROM agent_jobs j JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id" + clause + " ORDER BY t.created_at", params)).fetchall()
        return [_decode_job(row) for row in rows]

    async def ready_prepared_work_events(self,scene_id,cutoff):
        rows=await (await self._db.execute("""SELECT e.id FROM tasks t
            JOIN agent_jobs j ON j.id=t.id AND j.scene_id=t.scene_id
            JOIN events e ON e.scene_id=j.scene_id AND e.event_type='AGENT_JOB_FINISHED'
                AND json_extract(e.payload,'$.job_id')=j.id
                AND json_extract(e.payload,'$.job_revision')=j.revision
            WHERE t.scene_id=? AND t.status='result_ready'
                AND json_extract(t.payload,'$.delivery_action_id') IS NULL
                AND json_extract(j.result_json,'$.delivery') IS NOT NULL
            ORDER BY e.rowid""",(scene_id,))).fetchall()
        return await self.events_by_ids(scene_id,[row[0] for row in rows],cutoff)

    async def interrupt_job(self,job_id,scene_id,reason):
        """End unfinished execution without rewriting an existing delivery."""
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                job=await self.get_job(job_id,scene_id)
                if job is None or job['status'] not in {'pending','claimed','processing'}:
                    await self._db.rollback()
                    return None
                result=JobResult(status='interrupted',summary='原工作执行已中断，资料、预算和原始身份保留。',
                    result_ids=job['result_ids'],work_state=WorkState.model_validate(job['work_state']) if job['work_state'] else None,
                    unresolved=list(dict.fromkeys([*((job['work_state'] or {}).get('unresolved',[])),reason])),
                    reason='plugin_or_execution_interrupted')
                await self._db.execute("UPDATE agent_jobs SET result_json=?,updated_at=? WHERE id=? AND scene_id=?",
                    (result.model_dump_json(),self.clock(),job_id,scene_id))
                await self._db.execute("UPDATE tasks SET status='review_required' WHERE id=? AND scene_id=?",(job_id,scene_id))
                event=await self._queue_job_event(EventType.AGENT_JOB_CONTROL,job_id,scene_id,job['revision'],
                    {'operation':'interrupt','reason':reason,'result':result.model_dump(mode='json')})
                await self._db.commit()
                return event
            except BaseException:
                await self._db.rollback()
                raise

    async def _queue_job_event(self, kind, job_id, scene_id, revision, payload=None):
        job=await self.get_job(job_id,scene_id)
        event = Event(event_type=kind, scene_id=scene_id, actor_id="system:jobs", timestamp=self.clock(),
            payload={"job_id": job_id, "job_revision": revision,'plugin_origin':job['plugin_origin'] if job else None, **(payload or {})},
            metadata={"background_work": True})
        await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scene_id, event.model_dump_json()))
        return event

    async def validate_job_proposals_in_transaction(self, proposals, scene_id):
        """Read every control's original version before any operation is applied."""
        observed = {}
        for proposal in proposals:
            sources = list(dict.fromkeys(proposal.source_event_ids))
            rows = await (await self._db.execute(
                f"SELECT id,event_type FROM events WHERE scene_id=? AND id IN ({','.join('?' for _ in sources)})",
                [scene_id, *sources])).fetchall()
            if len(rows) != len(sources):
                raise ValueError("Job evidence outside scene")
            for result_id in proposal.result_ids:
                if await self.read_tool_observation(result_id, [scene_id]) is None:
                    raise ValueError("Transferred work result outside scene")
            if proposal.operation == "create":
                request = await (await self._db.execute(
                    'SELECT event_type,actor_id FROM events WHERE id=? AND scene_id=?',
                    (proposal.request_source_event_id,scene_id))).fetchone()
                if (request is None or request[0] not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                        or request[1] != 'user:' + proposal.requester_qq_uid
                        or proposal.request_source_event_id not in sources):
                    raise ValueError('Work requester must match its explicit human request source')
                continue
            if proposal.job_id in observed:
                raise ValueError('One transaction cannot control the same work more than once')
            current = await self.get_job(proposal.job_id, scene_id)
            if not current or current["revision"] != proposal.expected_revision:
                raise JobChanged("Job version conflict or outside scene")
            if proposal.requester_qq_uid != current['requester_qq_uid']:
                raise ValueError('Work controls cannot replace the original requester')
            if proposal.work_operation!=current['work_operation']:
                raise ValueError('Work controls cannot replace the original business operation')
            if proposal.operation!='cancel':self.plugin_work(current)
            if current["status"] in {"completed", "cancelled", "shadow_observed", "delivery_unknown"} and not (
                    proposal.operation=='resume' and current['can_resume']):
                raise ValueError("Job is no longer editable")
            if proposal.operation == "resume" and not current["can_resume"]:
                raise ValueError("Only interrupted, failed or settled partial work with an explicit unfinished scope can resume")
            if (proposal.plugin_origin.model_dump() if proposal.plugin_origin else None)!=current['plugin_origin']:
                raise ValueError('Work controls cannot replace its original plugin owner')
            if proposal.work_parameters is not None and current['work_operation']=='information':
                raise ValueError('Ordinary work has no plugin-specific parameters')
            observed[proposal.job_id] = current
        return observed

    async def apply_job_proposals_in_transaction(self, proposals, scene_id, episode_id, origin_mode, observed):
        """Apply the caller's already validated operations in its transaction."""
        tasks, references = [], {}
        for proposal in proposals:
            sources = list(dict.fromkeys(proposal.source_event_ids))
            if proposal.operation == "create":
                stable = f"{scene_id}:{proposal.request_source_event_id}:{proposal.proposal_id}"
                job_id = "job_" + uuid.uuid5(uuid.NAMESPACE_URL, stable).hex[:20]
                parameters = proposal.work_parameters
                existing = await self.get_job(job_id, scene_id)
                references[proposal.proposal_id] = job_id
                if existing:
                    if (existing['requester_qq_uid'] != proposal.requester_qq_uid
                            or existing['request_source_event_id'] != proposal.request_source_event_id
                            or existing['goal'] != proposal.goal
                            or existing['work_operation'] != proposal.work_operation
                            or existing['work_parameters'] != parameters
                            or existing['plugin_origin'] != (proposal.plugin_origin.model_dump() if proposal.plugin_origin else None)
                            or existing['constraints'] != list(dict.fromkeys(proposal.constraints_add))
                            or existing['status'] not in {'pending', 'claimed', 'processing'}):
                        raise ValueError('Job creation reference does not match this unchanged human request')
                    if existing['ack_action_id']:
                        raise ValueError('This work already has a committed confirmation; inspect its existing action')
                    continue
                progress=await self._new_work_progress(scene_id,proposal)
                payload = {"kind": "agent_job", "proposal_id": proposal.proposal_id, "source_event_ids": sources,
                           "work_operation":proposal.work_operation, "requester_qq_uid":proposal.requester_qq_uid,
                           "request_source_event_id":proposal.request_source_event_id,
                           "observation_reads":{},
                           'plugin_origin':proposal.plugin_origin.model_dump() if proposal.plugin_origin else None,
                           'work_parameters':parameters,'work_progress':progress}
                task = TaskItem(id=job_id, scene_id=scene_id, description=proposal.goal,
                    due_at=self.clock(), payload=payload, origin_episode_id=episode_id, origin_mode=origin_mode)
                await self._db.execute("""INSERT INTO tasks(id,scene_id,description,due_at,status,source_event_id,payload,created_at,origin_episode_id,origin_mode)
                    VALUES(?,?,?,?,'pending',?,?,?,?,?)""", (job_id, scene_id, proposal.goal, task.due_at, episode_id,
                        json.dumps(payload, ensure_ascii=False), self.clock(), episode_id, origin_mode))
                await self._db.execute("""INSERT INTO agent_jobs(id,scene_id,revision,goal,constraints_json,source_event_ids_json,result_ids_json,updated_at)
                    VALUES(?,?,1,?,?,?,?,?)""", (job_id, scene_id, proposal.goal,
                        json.dumps(list(dict.fromkeys(proposal.constraints_add)), ensure_ascii=False), json.dumps(sources),
                        json.dumps(list(dict.fromkeys(proposal.result_ids))), self.clock()))
                tasks.append(task)
                await self._queue_job_event(EventType.AGENT_JOB_CONTROL, job_id, scene_id, 1, {"operation": "create"})
                continue
            job_id = proposal.job_id
            current = observed[job_id]
            if proposal.proposal_id:
                references[proposal.proposal_id] = job_id
            revision = current["revision"] + 1
            goal = proposal.goal.strip() if proposal.goal else current["goal"]
            constraints = [value for value in current["constraints"] if value not in proposal.constraints_remove]
            constraints = list(dict.fromkeys(constraints + proposal.constraints_add))
            source_ids = list(dict.fromkeys(current["source_event_ids"] + sources))
            result_ids = list(dict.fromkeys(current["result_ids"] + proposal.result_ids))
            resume_from=({'revision':current['revision'],'result':current['result'],
                'response_status':current['status'],'delivery_action_id':current['delivery_action_id'],
                'delivery_event_id':current['delivery_event_id']} if proposal.operation=='resume' else None)
            if proposal.work_parameters is not None:
                progress=await self._new_work_progress(scene_id,proposal,current)
                await self._db.execute("""UPDATE tasks SET payload=json_set(payload,
                    '$.work_parameters',json(?),'$.work_progress',json(?)) WHERE id=? AND scene_id=?""",
                    (json.dumps(proposal.work_parameters,ensure_ascii=False),json.dumps(progress),job_id,scene_id))
            status = "cancelled" if proposal.operation == "cancel" else "processing" if current["status"] == "processing" else "pending"
            await self._db.execute("""UPDATE agent_jobs SET revision=?,goal=?,constraints_json=?,source_event_ids_json=?,result_ids_json=?,result_json=NULL,updated_at=? WHERE id=? AND scene_id=?""",
                (revision, goal, json.dumps(constraints, ensure_ascii=False), json.dumps(source_ids), json.dumps(result_ids), self.clock(), job_id, scene_id))
            await self._db.execute("""UPDATE tasks SET status=?,due_at=?,description=?,
                origin_mode=CASE WHEN ?='shadow' THEN 'shadow' ELSE origin_mode END,
                payload=json_remove(payload,'$.result','$.delivery_action_id','$.delivery_event_id') WHERE id=? AND scene_id=?""",
                (status, self.clock(), goal, origin_mode, job_id, scene_id))
            await self._db.execute("UPDATE tasks SET payload=json_set(payload,'$.resume_from',json(?)) WHERE id=? AND scene_id=?",
                (json.dumps(resume_from,ensure_ascii=False),job_id,scene_id))
            await self._queue_job_event(EventType.AGENT_JOB_CONTROL, job_id, scene_id, revision, {"operation": proposal.operation})
        return tasks, references

    async def record_job_presentations(self, job_id, scene_id, revision, presentations):
        """Append ranges actually sent to a work model, including an older goal's request."""
        shown = [ResultPresentation.model_validate(item) for item in presentations]
        if not shown:
            return
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                job = await self.get_job(job_id, scene_id)
                if not job or not 1 <= revision <= job['revision']:
                    raise JobChanged('Presented observations belong to an unknown work revision')
                reads = job['observation_reads']
                for presentation in shown:
                    if presentation.result_id not in job['result_ids']:
                        raise ValueError('A presented range must belong to this work')
                    total = await self._observation_size(presentation.result_id, scene_id, presentation.coordinate_unit)
                    if presentation.total != total:
                        raise ValueError('Presented range total does not match the stored observation')
                    units = reads.setdefault(presentation.result_id, {})
                    recorded = units.setdefault(presentation.coordinate_unit, {'total': total, 'ranges': []})
                    if recorded['total'] != total:
                        raise ValueError('Stored observation changed after its recorded presentation')
                    intervals = sorted([*recorded['ranges'], [presentation.start, presentation.end]])
                    merged = []
                    for left, right in intervals:
                        if merged and left <= merged[-1][1]:
                            merged[-1][1] = max(merged[-1][1], right)
                        else:
                            merged.append([left, right])
                    recorded['ranges'] = merged
                await self._db.execute("UPDATE tasks SET payload=json_set(payload,'$.observation_reads',json(?)) WHERE id=? AND scene_id=?",
                    (json.dumps(reads, ensure_ascii=False), job_id, scene_id))
                spec=self.plugin_work(job)
                if spec:
                    progress=await spec.adopt_reads(self,job,spec.parameters_model.model_validate(job['work_parameters']),
                        spec.progress_model.model_validate(job['work_progress']),shown)
                    progress=spec.progress_model.model_validate(progress).model_dump(mode='json')
                    await self._db.execute("UPDATE tasks SET payload=json_set(payload,'$.work_progress',json(?)) WHERE id=? AND scene_id=?",
                        (json.dumps(progress,ensure_ascii=False),job_id,scene_id))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def save_plugin_work_progress(self,job_id,scene_id,revision,progress):
        """Save plugin business progress under the original job revision."""
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                job=await self.get_job(job_id,scene_id)
                if not job or job['revision']!=revision or job['status']!='processing':
                    raise JobChanged('Plugin progress belongs to an obsolete work execution')
                spec=self.plugin_work(job)
                if spec is None or spec.execute is None:
                    raise ValueError('This work has no plugin execution entry')
                value=spec.progress_model.model_validate(progress)
                await self._db.execute("UPDATE tasks SET payload=json_set(payload,'$.work_progress',json(?)) WHERE id=? AND scene_id=?",
                    (value.model_dump_json(),job_id,scene_id))
                await self._db.execute('UPDATE agent_jobs SET updated_at=? WHERE id=? AND scene_id=?',
                    (self.clock(),job_id,scene_id))
                event=await self._queue_job_event(EventType.AGENT_JOB_CHECKPOINT,job_id,scene_id,revision,
                    {'phase':'plugin_progress','work_progress':spec.project_progress(value)})
                await self._db.commit()
                return event
            except BaseException:
                await self._db.rollback()
                raise

    async def _observation_size(self, result_id, scene_id, coordinate_unit):
        result = await self.read_tool_observation(result_id, [scene_id])
        if result is None:
            raise ValueError('Result does not exist in this work scene')
        if coordinate_unit == 'characters':
            return len(result.content)
        records = json.loads(result.content)
        if not isinstance(records, list):
            raise ValueError('Record coordinates require a stored JSON array')
        return len(records)

    async def _validate_evidence_spans(self, job, spans, result_ids):
        for span in spans:
            if span.result_id not in result_ids or span.result_id not in job['result_ids']:
                raise ValueError('Evidence span must refer to one of this finding\'s work observations')
            total = await self._observation_size(span.result_id, job['scene_id'], span.coordinate_unit)
            if span.start==span.end and total:
                raise ValueError('Evidence must include content from a nonempty observation')
            recorded = job['observation_reads'].get(span.result_id, {}).get(span.coordinate_unit)
            if not recorded or recorded['total'] != total or span.end > total:
                raise ValueError('Evidence span has no matching actual presentation record')
            if not any(left <= span.start <= span.end <= right for left, right in recorded['ranges']):
                raise ValueError('Evidence span includes content not actually provided to this work model')

    async def validate_job_message(self, scene_id, job_id, revision, fulfil=False):
        job = await self.get_job(job_id, scene_id)
        if not job or job["revision"] != revision:
            raise JobChanged("Reply refers to a missing work or obsolete revision")
        if fulfil and (job["status"] not in {"result_ready", "awaiting_delivery"}
                       or job["execution_status"] not in {"completed", "partial"}):
            raise ValueError("Job fulfilment requires a completed or partial result ready for delivery")
        return job

    async def report_job_progress(self, job_id, scene_id, revision, summary, result_ids, *, min_interval_seconds):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    raise JobChanged("Progress belongs to an obsolete job")
                if not summary.strip() or len(summary) > 1200 or not result_ids or not set(result_ids).issubset(job["result_ids"]):
                    raise ValueError("Progress needs concise findings and obtained result IDs")
                row = await (await self._db.execute("SELECT payload FROM tasks WHERE id=? AND scene_id=?", (job_id, scene_id))).fetchone()
                payload = json.loads(row[0])
                if payload.get("progress_at") is not None and self.clock()-payload["progress_at"] < min_interval_seconds:
                    await self._db.rollback()
                    return None
                await self._db.execute("UPDATE tasks SET payload=json_set(payload,'$.progress',?,'$.progress_at',?) WHERE id=? AND scene_id=?",
                    (summary, self.clock(), job_id, scene_id))
                event = await self._queue_job_event(EventType.AGENT_JOB_PROGRESS, job_id, scene_id, revision,
                    {"raw_text": "信息工作有新的资料进展，是否表达由社会认知判断。", "summary": summary, "result_ids": result_ids, "origin_mode": job["origin_mode"]})
                await self._db.commit()
                return event
            except BaseException:
                await self._db.rollback()
                raise

    async def job_checkpoint(self, job_id, scene_id, revision, *, model_steps=0, tool_calls=0,
                             elapsed_seconds=0, result_ids=(), limits=None):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    raise JobChanged("Job changed during execution")
                steps, calls = job["model_steps"] + model_steps, job["tool_calls"] + tool_calls
                elapsed = job["elapsed_seconds"] + max(0, elapsed_seconds)
                if limits:
                    if steps>limits[0]:raise JobBudgetExhausted('Work model-step budget exhausted')
                    if calls>limits[1]:raise JobBudgetExhausted('Work tool-call budget exhausted',budget_kind='tool_calls')
                    if elapsed>limits[2] or model_steps and elapsed>=limits[2]:
                        raise JobBudgetExhausted('Work elapsed-time budget exhausted',budget_kind='elapsed_time')
                ids = list(dict.fromkeys(job["result_ids"] + list(result_ids)))
                for result_id in ids:
                    if await self.read_tool_observation(result_id, [scene_id]) is None:
                        raise ValueError("Job checkpoint has foreign observation")
                await self._db.execute("UPDATE agent_jobs SET model_steps=?,tool_calls=?,elapsed_seconds=?,result_ids_json=?,updated_at=? WHERE id=? AND scene_id=?",
                    (steps, calls, elapsed, json.dumps(ids), self.clock(), job_id, scene_id))
                event = await self._queue_job_event(EventType.AGENT_JOB_CHECKPOINT, job_id, scene_id, revision)
                await self._db.commit()
                return event
            except BaseException:
                await self._db.rollback()
                raise

    async def complete_job(self, job_id, scene_id, revision, result: JobResult, *, work_state=None, skill_candidate=None):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    await self._db.rollback()
                    return None
                try:
                    spec=self.plugin_work(job) if result.status in {'completed','partial'} or skill_candidate else None
                    if spec:
                        result=spec.finalize(spec.parameters_model.model_validate(job['work_parameters']),
                            spec.progress_model.model_validate(job['work_progress']),result)
                        result=JobResult.model_validate(result)
                    if not set(result.result_ids).issubset(job["result_ids"]):
                        raise ValueError("Job summary cites observations it did not obtain")
                    if result.delivery:
                        if spec is None or spec.execute is None or result.status not in {'completed','partial'}:
                            raise ValueError('Prepared delivery requires a completed plugin execution result')
                        if result.delivery.result_id not in result.result_ids:
                            raise ValueError('Prepared delivery must reference this work result')
                        for segment in result.delivery.segments:
                            if segment.type=='image' and await self.get_media(segment.asset_id,[scene_id]) is None:
                                raise ValueError('Prepared work image is not registered in this scene')
                    await self._validate_evidence_spans(job, result.evidence_spans, result.result_ids)
                    if result.work_state is not None:
                        await self._validate_work_state(job, result.work_state)
                    if work_state is not None:
                        if work_state.goal_revision != revision:
                            raise ValueError('Work state belongs to an obsolete goal')
                        await self._validate_work_state(job, work_state)
                        await self._db.execute("UPDATE agent_jobs SET work_state_json=? WHERE id=? AND scene_id=?", (work_state.model_dump_json(), job_id, scene_id))
                    if skill_candidate is not None:
                        if spec and not spec.allow_learning:
                            raise ValueError('This plugin work does not create procedural skills')
                        await self.add_skill_candidate_in_transaction(job, skill_candidate)
                except ValueError as error:
                    raise JobResultRejected(str(error)) from error
                await self._db.execute("UPDATE agent_jobs SET result_json=?,updated_at=? WHERE id=? AND scene_id=?",
                    (result.model_dump_json(), self.clock(), job_id, scene_id))
                await self._db.execute("UPDATE tasks SET status='result_ready',payload=json_set(payload,'$.result',?) WHERE id=? AND scene_id=?",
                    (result.summary, job_id, scene_id))
                event = await self._queue_job_event(EventType.AGENT_JOB_FINISHED, job_id, scene_id, revision,
                    {"raw_text": "信息工作已有结果，结合最新要求核对后决定如何回应。", "result": result.model_dump(), "origin_mode": job["origin_mode"]})
                await self._db.commit()
                return event
            except BaseException:
                await self._db.rollback()
                raise

    async def _validate_work_state(self, job, state: WorkState):
        if state.goal_revision > job["revision"]:
            raise ValueError("Work state belongs to an unknown goal revision")
        ids = [*state.key_result_ids, *(ident for step in state.completed_steps for ident in step.result_ids)]
        if not set(ids).issubset(job["result_ids"]):
            raise ValueError("Completed work steps require this work's actual observations")
        await self._validate_evidence_spans(job, state.evidence_spans, state.key_result_ids)
        for step in state.completed_steps:
            await self._validate_evidence_spans(job, step.evidence_spans, step.result_ids)

    async def update_work_state(self, job_id, scene_id, revision, state: WorkState, skill_candidate=None):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    raise JobChanged("Work state belongs to obsolete work")
                if state.goal_revision != revision:
                    raise ValueError('Work state belongs to an obsolete goal')
                await self._validate_work_state(job, state)
                if skill_candidate is not None:
                    spec=self.plugin_work(job)
                    if spec and not spec.allow_learning:
                        raise ValueError('This plugin work does not create procedural skills')
                    await self.add_skill_candidate_in_transaction(job, skill_candidate)
                await self._db.execute("UPDATE agent_jobs SET work_state_json=?,updated_at=? WHERE id=? AND scene_id=?",
                    (state.model_dump_json(), self.clock(), job_id, scene_id))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def bind_job_model(self, job_id, scene_id, revision, binding):
        async with self._write_lock:
            cursor = await self._db.execute("""UPDATE agent_jobs SET model_binding_json=? WHERE id=? AND scene_id=? AND revision=?
                AND model_binding_json IS NULL AND EXISTS(SELECT 1 FROM tasks WHERE tasks.id=agent_jobs.id AND status='processing')""",
                (json.dumps(binding), job_id, scene_id, revision))
            if cursor.rowcount != 1:
                await self._db.rollback()
                raise JobChanged("Work binding already exists or work changed")
            await self._db.commit()

    async def read_job_checkpoint(self, job_id, scene_id):
        """Private runtime continuation; never expose it through presentation APIs."""
        row = await (await self._db.execute("SELECT checkpoint_json FROM agent_jobs WHERE id=? AND scene_id=?", (job_id, scene_id))).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    async def save_job_exchange(self, job_id, scene_id, revision, trajectory, exchange_count):
        from len_bot.runtime.work_context import archive_trajectory, exchange_spans
        spans = exchange_spans(trajectory)
        if not spans or type(exchange_count) is not int or exchange_count < 1:
            raise ValueError('A work checkpoint needs a numbered complete native tool exchange')
        native = {"goal_revision": revision, "exchange_count": exchange_count, "updated_at": self.clock(),
                  "messages": archive_trajectory(trajectory)}
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or not 1 <= revision <= job['revision']:
                    raise JobChanged('Tool exchange belongs to an unknown work revision')
                result_ids = list(job['result_ids'])
                for start, end in spans:
                    for message in trajectory[start+1:end]:
                        try:
                            result = json.loads(message['content'])
                        except (TypeError, ValueError):
                            continue
                        result_id = result.get('result_id') if isinstance(result, dict) else None
                        if result_id and result_id not in result_ids:
                            if await self.read_tool_observation(result_id, [scene_id]) is None:
                                raise ValueError('Complete exchange contains an unknown or foreign observation')
                            result_ids.append(result_id)
                checkpoint = await self.read_job_checkpoint(job_id, scene_id)
                latest_calls = [call['id'] for call in trajectory[spans[-1][0]]['tool_calls']]
                same_exchange = False
                if checkpoint and checkpoint['goal_revision']==revision and checkpoint['exchange_count']==exchange_count:
                    prior_spans=exchange_spans(checkpoint['messages'])
                    same_exchange=bool(prior_spans and latest_calls==[
                        call['id'] for call in checkpoint['messages'][prior_spans[-1][0]]['tool_calls']])
                # A control can commit while the one worker awaits a tool. Keep
                # that completed exchange without replacing a newer goal's tail.
                replace = (checkpoint is None or checkpoint['goal_revision'] < revision
                    or checkpoint['goal_revision'] == revision and checkpoint['exchange_count'] < exchange_count
                    or same_exchange)
                if replace:
                    await self._db.execute('UPDATE agent_jobs SET checkpoint_json=? WHERE id=? AND scene_id=?',
                        (json.dumps(native, ensure_ascii=False), job_id, scene_id))
                    checkpoint = native
                await self._db.execute('UPDATE agent_jobs SET result_ids_json=?,updated_at=? WHERE id=? AND scene_id=?',
                    (json.dumps(result_ids), self.clock(), job_id, scene_id))
                sequence = exchange_count
                # A late older exchange is archival fact, never a new active turn.
                prior = await (await self._db.execute('SELECT goal_revision,messages_json FROM job_exchanges WHERE job_id=? AND sequence=?',
                        (job_id,sequence))).fetchone()
                refine=False
                if prior and prior[0]==revision:
                    prior_messages=json.loads(prior[1])
                    prior_spans=exchange_spans(prior_messages)
                    refine=bool(prior_spans and latest_calls==[
                        call['id'] for call in prior_messages[prior_spans[-1][0]]['tool_calls']])
                if prior and not refine:
                    sequence = (await (await self._db.execute('SELECT MAX(sequence) FROM job_exchanges WHERE job_id=?',
                        (job_id,))).fetchone())[0] + 1
                _, end = spans[-1]
                start = spans[-2][1] if len(spans) > 1 else min(2, spans[-1][0])
                encoded=json.dumps(archive_trajectory(trajectory[start:end]),ensure_ascii=False)
                if refine:
                    # The same completed calls first save locator receipts, then
                    # their budgeted presentation. Immutable observations and
                    # the native assistant response remain unchanged.
                    await self._db.execute('UPDATE job_exchanges SET messages_json=? WHERE job_id=? AND sequence=?',
                        (encoded,job_id,sequence))
                else:
                    await self._db.execute("INSERT INTO job_exchanges VALUES(?,?,?,?,?)",
                        (job_id, scene_id, sequence, revision, encoded))
                await self._db.commit()
                return checkpoint['exchange_count']
            except BaseException:
                await self._db.rollback()
                raise

    async def save_job_compression(self, job_id, scene_id, revision, compression, *, trajectory=None):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                cursor = await self._db.execute("""UPDATE agent_jobs SET compression_json=? WHERE id=? AND scene_id=? AND revision=?
                    AND EXISTS(SELECT 1 FROM tasks WHERE tasks.id=agent_jobs.id AND status='processing')""",
                    (json.dumps(compression, ensure_ascii=False), job_id, scene_id, revision))
                if cursor.rowcount != 1:
                    raise JobChanged("Compression belongs to obsolete work")
                if trajectory is not None:
                    from len_bot.runtime.work_context import archive_trajectory, validate_complete_exchanges
                    validate_complete_exchanges(trajectory)
                    checkpoint = await self.read_job_checkpoint(job_id, scene_id)
                    if checkpoint:
                        checkpoint["messages"] = archive_trajectory(trajectory)
                        await self._db.execute("UPDATE agent_jobs SET checkpoint_json=? WHERE id=? AND scene_id=?", (json.dumps(checkpoint, ensure_ascii=False), job_id, scene_id))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def pin_job_skill(self, job_id, scene_id, revision, skill_id):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    raise JobChanged("Skill read belongs to obsolete work")
                skill = await self.read_skill(skill_id, scene_id, job["skill_versions"].get(skill_id))
                if not skill:
                    raise ValueError("Skill unavailable in this scene")
                pins = {**job["skill_versions"], skill_id: skill["version"]}
                await self._db.execute("UPDATE agent_jobs SET skill_versions_json=? WHERE id=? AND scene_id=?", (json.dumps(pins), job_id, scene_id))
                await self._db.commit()
                return skill
            except BaseException:
                await self._db.rollback()
                raise

    async def charge_skill_maintenance(self, job_id, scene_id, revision, *, model_steps=0, elapsed_seconds=0, limits=None):
        """Learning uses the originating work budget, including after its result."""
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or (model_steps and (job["revision"] != revision or job["status"] == "cancelled")):
                    raise JobChanged("Skill source job changed")
                steps, elapsed = job["model_steps"] + model_steps, job["elapsed_seconds"] + elapsed_seconds
                if limits:
                    if steps>limits[0]:raise JobBudgetExhausted('Work model-step budget exhausted before skill maintenance')
                    if elapsed>limits[2] or model_steps and elapsed>=limits[2]:
                        raise JobBudgetExhausted('Work elapsed-time budget exhausted before skill maintenance',budget_kind='elapsed_time')
                await self._db.execute("UPDATE agent_jobs SET model_steps=?,elapsed_seconds=? WHERE id=? AND scene_id=?", (steps, elapsed, job_id, scene_id))
                await self._db.commit()
                return {"model_steps": steps, "elapsed_seconds": elapsed}
            except BaseException:
                await self._db.rollback()
                raise

    async def record_job_elapsed(self, job_id, scene_id, elapsed_seconds, *, result_ids=()):
        """Keep actual costs and completed reads across revision/cancellation."""
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                job=await self.get_job(job_id,scene_id)
                if job is None:raise JobChanged('Work no longer exists for recording its completed execution')
                ids=list(dict.fromkeys([*job['result_ids'],*result_ids]))
                for ident in result_ids:
                    if await self.read_tool_observation(ident,[scene_id]) is None:
                        raise ValueError('Completed work read is missing or outside its scene')
                await self._db.execute('UPDATE agent_jobs SET elapsed_seconds=elapsed_seconds+?,result_ids_json=? WHERE id=? AND scene_id=?',
                    (max(0,elapsed_seconds),json.dumps(ids),job_id,scene_id))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
