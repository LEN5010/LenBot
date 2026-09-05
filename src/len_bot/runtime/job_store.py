"""Work detail belongs to tasks; budgets and observations survive revision/restart."""
import json
import uuid

from len_bot.cognition.jobs import JobChanged, JobBudgetExhausted, JobResult
from len_bot.events.models import Event, EventType
from len_bot.scheduler.models import TaskItem


def _decode_job(row):
    if not row:
        return None
    fields = ["id", "scene_id", "revision", "goal", "constraints", "source_event_ids", "result_ids",
              "model_steps", "tool_calls", "elapsed_seconds", "result", "updated_at", "status", "origin_mode", "created_at"]
    data = dict(zip(fields, row))
    for field in ("constraints", "source_event_ids", "result_ids", "result"):
        data[field] = json.loads(data[field]) if data[field] else None
    return data


class JobStoreMixin:
    async def initialize_jobs(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS agent_jobs (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, revision INTEGER NOT NULL,
            goal TEXT NOT NULL, constraints_json TEXT NOT NULL, source_event_ids_json TEXT NOT NULL,
            result_ids_json TEXT NOT NULL, model_steps INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL NOT NULL DEFAULT 0,
            result_json TEXT, updated_at REAL NOT NULL)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_agent_jobs_scene ON agent_jobs(scene_id,updated_at)")

    async def get_job(self, job_id, scene_id):
        row = await (await self._db.execute("""SELECT j.*,t.status,t.origin_mode,t.created_at FROM agent_jobs j
            JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id WHERE j.id=? AND j.scene_id=?""", (job_id, scene_id))).fetchone()
        return _decode_job(row)

    async def list_jobs(self, scene_id=None):
        clause, params = (" WHERE j.scene_id=?", (scene_id,)) if scene_id is not None else ("", ())
        rows = await (await self._db.execute("SELECT j.*,t.status,t.origin_mode,t.created_at FROM agent_jobs j JOIN tasks t ON j.id=t.id AND j.scene_id=t.scene_id" + clause + " ORDER BY t.created_at", params)).fetchall()
        return [_decode_job(row) for row in rows]

    async def _queue_job_event(self, kind, job_id, scene_id, revision, payload=None):
        event = Event(event_type=kind, scene_id=scene_id, actor_id="system:jobs", timestamp=self.clock(),
            payload={"job_id": job_id, "job_revision": revision, **(payload or {})},
            metadata={"background_work": True})
        await self._db.execute("INSERT INTO pending_runtime_events VALUES(?,?,?)", (event.id, scene_id, event.model_dump_json()))
        return event

    async def apply_job_proposals_in_transaction(self, proposals, scene_id, episode_id, origin_mode):
        """Caller owns BEGIN/COMMIT and all accompanying session/message proposals."""
        tasks, references = [], {}
        for proposal in proposals:
            sources = list(dict.fromkeys(proposal.source_event_ids))
            rows = await (await self._db.execute(
                f"SELECT id,event_type FROM events WHERE scene_id=? AND id IN ({','.join('?' for _ in sources)})",
                [scene_id, *sources])).fetchall()
            if len(rows) != len(sources):
                raise ValueError("Job evidence outside scene")
            if proposal.operation == "create" and not any(kind in {
                "GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "LIVE_STARTED", "LIVE_ENDED", "OPERATOR_ACTION"
            } for _, kind in rows):
                raise ValueError("A work result cannot autonomously create another job")
            for result_id in proposal.result_ids:
                if await self.read_tool_observation(result_id, [scene_id]) is None:
                    raise ValueError("Transferred work result outside scene")
            if proposal.operation == "create":
                stable = f"{scene_id}:{proposal.proposal_id}:{','.join(sorted(sources))}"
                job_id = "job_" + uuid.uuid5(uuid.NAMESPACE_URL, stable).hex[:20]
                existing = await self.get_job(job_id, scene_id)
                references[proposal.proposal_id] = job_id
                if existing:
                    if existing["goal"] != proposal.goal or existing["status"] not in {"pending", "claimed", "processing"}:
                        raise ValueError("Job creation reference has already been used; inspect the existing job")
                    continue
                payload = {"kind": "agent_job", "proposal_id": proposal.proposal_id, "source_event_ids": sources}
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
            current = await self.get_job(job_id, scene_id)
            if not current or current["revision"] != proposal.expected_revision:
                raise ValueError("Job version conflict or outside scene")
            if current["status"] in {"completed", "cancelled", "shadow_observed", "delivery_unknown"}:
                raise ValueError("Job is no longer editable")
            if proposal.operation == "resume" and current["status"] not in {"review_required", "failed"}:
                raise ValueError("Only an interrupted/failed job can resume")
            revision = current["revision"] + 1
            goal = proposal.goal.strip() if proposal.goal else current["goal"]
            constraints = [value for value in current["constraints"] if value not in proposal.constraints_remove]
            constraints = list(dict.fromkeys(constraints + proposal.constraints_add))
            source_ids = list(dict.fromkeys(current["source_event_ids"] + sources))
            result_ids = list(dict.fromkeys(current["result_ids"] + proposal.result_ids))
            status = "cancelled" if proposal.operation == "cancel" else "processing" if current["status"] == "processing" else "pending"
            await self._db.execute("""UPDATE agent_jobs SET revision=?,goal=?,constraints_json=?,source_event_ids_json=?,result_ids_json=?,result_json=NULL,updated_at=? WHERE id=? AND scene_id=?""",
                (revision, goal, json.dumps(constraints, ensure_ascii=False), json.dumps(source_ids), json.dumps(result_ids), self.clock(), job_id, scene_id))
            await self._db.execute("""UPDATE tasks SET status=?,due_at=?,description=?,
                origin_mode=CASE WHEN ?='shadow' THEN 'shadow' ELSE origin_mode END,
                payload=json_remove(payload,'$.result','$.delivery_action_id','$.delivery_event_id') WHERE id=? AND scene_id=?""",
                (status, self.clock(), goal, origin_mode, job_id, scene_id))
            await self._queue_job_event(EventType.AGENT_JOB_CONTROL, job_id, scene_id, revision, {"operation": proposal.operation})
        return tasks, references

    async def validate_job_message(self, scene_id, job_id, revision, fulfil=False):
        job = await self.get_job(job_id, scene_id)
        if not job or job["revision"] != revision or job["status"] not in {"pending", "claimed", "processing", "result_ready", "awaiting_delivery"}:
            raise ValueError("Reply refers to an obsolete or cancelled job")
        if fulfil and not job["result"]:
            raise ValueError("Job has not produced a result")
        return job

    async def report_job_progress(self, job_id, scene_id, revision, summary, result_ids):
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
                if payload.get("progress_at") is not None and self.clock()-payload["progress_at"] < 30:
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
                if limits and (steps > limits[0] or calls > limits[1] or elapsed > limits[2]):
                    raise JobBudgetExhausted("Work budget exhausted")
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

    async def complete_job(self, job_id, scene_id, revision, result: JobResult):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                job = await self.get_job(job_id, scene_id)
                if not job or job["revision"] != revision or job["status"] != "processing":
                    await self._db.rollback()
                    return None
                if not set(result.result_ids).issubset(job["result_ids"]):
                    raise ValueError("Job summary cites observations it did not obtain")
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
