"""Work detail belongs to tasks; budgets and observations survive revision/restart."""
import json
import uuid

from len_bot.cognition.jobs import JobChanged, JobBudgetExhausted, JobResultRejected, JobResult, ResultPresentation, WorkState, ReusedWorkResult
from len_bot.events.models import Event, EventType
from len_bot.scheduler.models import TaskItem
from len_bot.skills.store import SkillStoreMixin


def _typed_initiator(task_payload):
    """Read a stored initiator, converting an older record from its own fields."""
    from pydantic import TypeAdapter
    from len_bot.events.models import Initiator, legacy_initiator
    stored = task_payload.get('initiator')
    if stored is not None:
        return TypeAdapter(Initiator).validate_python(stored)
    return legacy_initiator(task_payload.get('requester_qq_uid'), task_payload.get('request_source_event_id'))


def _decode_job(row):
    if not row:
        return None
    fields = ["id", "scene_id", "revision", "goal", "constraints", "source_event_ids", "result_ids",
              "model_steps", "tool_calls", "elapsed_seconds", "result", "updated_at",
              "work_state", "model_binding", "checkpoint_data", "compression", "skill_versions", "budget",
              "status", "origin_mode", "created_at", "task_payload"]
    data = dict(zip(fields, row))
    for field in ("constraints", "source_event_ids", "result_ids", "result", "work_state", "model_binding", "checkpoint_data", "compression", "skill_versions", "budget"):
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
    data['public_research'] = task_payload.get('public_research')
    data['resume_from'] = task_payload.get('resume_from')
    reused=task_payload.get('reused_work')
    data['reused_work'] = ReusedWorkResult.model_validate(reused).model_dump(mode='json') if reused is not None else None
    # Older work keeps its exact requester and request anchor; convert those
    # two fields into the typed human initiator rather than guessing one.
    # Records without a definite anchor keep no initiator at all.
    data['initiator'] = (initiator.model_dump(mode='json') if (initiator := _typed_initiator(task_payload)) else None)
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

    def billing_subject_for(self, initiator, scene_id: str) -> str:
        """Who pays for a work, taken from its typed initiator.

        A reservation is only ever opened for a typed identity: a human's own
        request bills that real UID, and a system or plugin origin bills its
        own explicit scope.  An unestablished identity raises instead of
        falling back to a group-wide or empty account.
        """
        from len_bot.runtime.capabilities import subject_for
        return subject_for(initiator, scene_id).billing_subject

    def reservation_policy_for(self, initiator, scene_id: str, now: float, *,
                               work_operation: str = 'information'):
        """The quota policy a work runs under, plus the grant that named it.

        The grant is the one that authorized this work's own capability, not
        an unrelated long_work row.  A named policy that no longer exists is
        refused rather than replaced by the empty default.
        """
        from len_bot.cognition.budget import ReservationPolicy
        from len_bot.runtime.capabilities import Capability, subject_for
        base = getattr(self, 'reservation_policy', None) or ReservationPolicy()
        authority = getattr(self, 'capability_authority', None)
        if authority is None:
            return base, None, None
        try:
            subject = subject_for(initiator, scene_id)
        except ValueError:
            return base, None, None
        # The operation mapping (e.g. information → public_research) is the
        # requirement for autonomous, non-human sources.  A human's work is
        # authorized by the chat whitelist and billed under the human's own
        # long_work grant, so the human branch keeps selecting by LONG_WORK
        # instead of skipping the grant because a system capability exists.
        if subject.principal_type == 'human':
            candidates = (Capability.LONG_WORK,)
        else:
            candidates = authority.required_for_work(work_operation) or (Capability.LONG_WORK,)
        grant = None
        for capability in candidates:
            grant = authority.grant_for(subject, capability, now=now)
            if grant is not None:
                break
        named = authority.policy_for_grant(grant)
        if named is not None:
            return named, grant.grant_id, grant
        return base, grant.grant_id if grant is not None else None, grant

    def budget_snapshot(self, *, token_limit: int | None = None):
        """The execution and ceiling facts a work is created under.

        Written once, at creation, and kept with the work: the copy in
        `agent_jobs.checkpoint_json` is what a later revision or resume reads
        its own limits from, so an operator who edits the default policy in
        between changes the next new work and not this one.  `token_limit` is
        filled from the reservation that was opened in the same transaction,
        which is why it is a parameter rather than a field read off the
        current configuration.
        """
        from len_bot.cognition.budget import WorkBudgetSnapshot
        config = getattr(self, 'budget_config', None)
        if config is None:
            raise ValueError('创建工作时没有可用的运行预算配置，不能为该工作记录执行上限')
        return WorkBudgetSnapshot(
            job_max_steps=config.job_max_steps, job_max_tool_calls=config.job_max_tool_calls,
            job_max_seconds=config.job_max_seconds, job_context_tokens=config.job_context_tokens,
            work_output_tokens=config.work_output_tokens,
            maintenance_context_tokens=config.maintenance_context_tokens,
            maintenance_output_tokens=config.maintenance_output_tokens,
            token_limit=token_limit)

    async def reserve_job_budget_in_transaction(self, job_id, scene_id, initiator, *,
                                                work_operation: str = 'information'):
        """Hold this work's budget inside the caller's existing write transaction.

        Called from `apply_job_proposals_in_transaction`, which already runs
        inside `commit_proposal_transaction`'s write transaction, so the work
        row and its hold become durable together or not at all.  Two works
        created at the same moment therefore cannot both read the same free
        balance and spend it twice.

        The hold and the work's recorded ceiling are the same number.  A work
        whose count dimension is unlimited still has one when the policy sets
        a per-work ceiling, which is what keeps a finite daily quota
        reservable; when neither exists the hold is None and the work's own
        deadline and message limits are its whole stopping condition.
        """
        policy, grant_id, grant = self.reservation_policy_for(
            initiator, scene_id, self.clock(), work_operation=work_operation)
        if grant is not None and grant.concurrency is not None:
            active = await self.count_subject_active_jobs(
                self.billing_subject_for(initiator, scene_id), exclude_job_id=job_id)
            if active >= grant.concurrency:
                raise ValueError(
                    f'该主体并发工作已达授予上限 {grant.concurrency}；请等待已有工作结束，或由运营者修订授予')
        snapshot = self.budget_snapshot()
        tokens = policy.work_reservation(model_steps=snapshot.job_max_steps,
            context_tokens=snapshot.job_context_tokens, output_tokens=snapshot.work_output_tokens)
        if tokens is None:
            # No token dimension at all.  The row is still written, with a
            # null ceiling and a null hold, so the work can be found, settled
            # and displayed like any other rather than looking like a work
            # that predates the reservation regime.
            await self.reserve_work_in_transaction(
                job_id=job_id, scene_id=scene_id,
                subject=self.billing_subject_for(initiator, scene_id),
                day_key=policy.day_key(self.clock(), getattr(self, 'billing_timezone', None)),
                tokens=0, limit_tokens=None, policy_name=grant_id,
                scene_limit=policy.daily_scene_token_limit, daily_limit=policy.daily_user_token_limit)
            return None
        await self.reserve_work_in_transaction(
            job_id=job_id, scene_id=scene_id,
            subject=self.billing_subject_for(initiator, scene_id),
            day_key=policy.day_key(self.clock(), getattr(self, 'billing_timezone', None)),
            tokens=tokens, policy_name=grant_id,
            scene_limit=policy.daily_scene_token_limit, daily_limit=policy.daily_user_token_limit)
        return tokens

    async def rehold_job_budget_in_transaction(self, job_id, initiator, scene_id, *,
                                               work_operation: str = 'information'):
        """Put a continued work back on hold under the ceiling it was created with.

        A resume or a revise makes the work executable again, so it needs a
        live hold again — but under the grant the work already carries, never
        under today's default.  What it may still spend is its recorded
        ceiling minus what it already put on its own account, so continuing a
        work that consumed most of its allowance does not require the day to
        cover that allowance a second time.  The initiator is the stored one,
        so a continuation cannot bill a different account, and the hold keeps
        the day the work was accepted on.  The grant's concurrency counts here
        the same way it counts at creation: a continuation occupies a slot
        exactly like a new work does.
        """
        if initiator is None:
            raise ValueError('This work has no typed initiator; continuing it would have no account to hold against')
        policy, _grant_id, grant = self.reservation_policy_for(
            initiator, scene_id, self.clock(), work_operation=work_operation)
        if grant is not None and grant.concurrency is not None:
            active = await self.count_subject_active_jobs(
                self.billing_subject_for(initiator, scene_id), exclude_job_id=job_id)
            if active >= grant.concurrency:
                raise ValueError(
                    f'该主体并发工作已达授予上限 {grant.concurrency}；请等待已有工作结束，或由运营者修订授予')
        await self.rehold_work_in_transaction(
            job_id=job_id, scene_limit=policy.daily_scene_token_limit,
            daily_limit=policy.daily_user_token_limit)


    async def record_job_deadline(self, job_id, scene_id, deadline_at: float) -> None:
        """Save the instant this work's window closes, the first time it starts.

        Written once and never rewritten: a revision, a resume or a restart
        continues against the same instant the work was granted, which is what
        keeps queueing and stopping from buying extra time.  A later control
        that grants more time is a separate operator decision and would write
        its own fact rather than quietly extend this one.
        """
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                row = await (await self._db.execute(
                    'SELECT budget_json FROM agent_jobs WHERE id=? AND scene_id=?', (job_id, scene_id))).fetchone()
                if row is None or not row[0]:
                    await self._db.rollback()
                    return
                budget = json.loads(row[0])
                if budget.get('deadline_at') is not None:
                    await self._db.rollback()
                    return
                budget['deadline_at'] = deadline_at
                await self._db.execute('UPDATE agent_jobs SET budget_json=? WHERE id=? AND scene_id=?',
                    (json.dumps(budget, ensure_ascii=False), job_id, scene_id))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def settle_job_budget(self, job_id, reason: str = ''):
        """End this work's hold: give back the unused part, keep what it spent.

        A work cancelled before its first model call gives the whole hold back.
        One that already called a provider keeps those tokens on its account,
        so cancelling is never a way to erase spent budget.  `reason` records
        why a never-used hold was released.
        """
        config = getattr(self, 'budget_config', None)
        output = config.work_output_tokens if config is not None else 0
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                await self.close_reservation_in_transaction(
                    job_id, conservative_output_tokens=output)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def count_subject_active_jobs(self, subject: str, *, exclude_job_id: str | None = None) -> int:
        """How many of this account's works are still occupying a live hold."""
        row = await (await self._db.execute(
            """SELECT COUNT(*) FROM usage_reservations r
               JOIN tasks t ON t.id=r.job_id AND t.scene_id=r.scene_id
               WHERE r.subject=? AND r.status='held'
                 AND t.status IN ('pending','claimed','processing')
                 AND (? IS NULL OR r.job_id!=?)""",
            (subject, exclude_job_id, exclude_job_id))).fetchone()
        return int(row[0] or 0)

    async def job_reservation(self, job_id):
        names = ["job_id", "scene_id", "subject", "day_key", "policy_name", "reserved_tokens", "limit_tokens",
                 "status", "usage_tokens", "estimated_tokens", "created_at", "settled_at"]
        row = await (await self._db.execute(
            f"SELECT {','.join(names)} FROM usage_reservations WHERE job_id=?", (job_id,))).fetchone()
        return dict(zip(names, row)) if row else None

    async def list_job_reservations(self, scene_id=None, *, subject=None, day_key=None, limit=100):
        clause, params = "", []
        for column, value in (("scene_id", scene_id), ("subject", subject), ("day_key", day_key)):
            if value is not None:
                clause += f" AND {column}=?"; params.append(value)
        rows = await (await self._db.execute(
            "SELECT job_id,scene_id,subject,day_key,policy_name,reserved_tokens,limit_tokens,status,usage_tokens,"
            f"estimated_tokens,created_at,settled_at FROM usage_reservations WHERE 1=1{clause}"
            " ORDER BY created_at DESC,job_id DESC LIMIT ?", [*params, limit])).fetchall()
        names = ["job_id", "scene_id", "subject", "day_key", "policy_name", "reserved_tokens", "limit_tokens",
                 "status", "usage_tokens", "estimated_tokens", "created_at", "settled_at"]
        return [dict(zip(names, row)) for row in rows]

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
            compression_json TEXT, skill_versions_json TEXT, budget_json TEXT)""")
        # The execution and ceiling facts a work was created under.  A work
        # without them is one that predates this column: it keeps whatever
        # counters it has and is never given a re-derived limit, because the
        # number it was granted is not recoverable from today's policy.
        columns = {column[1] for column in await (await self._db.execute("PRAGMA table_info(agent_jobs)")).fetchall()}
        if 'budget_json' not in columns:
            await self._db.execute('ALTER TABLE agent_jobs ADD COLUMN budget_json TEXT')
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
                config = getattr(self, 'budget_config', None)
                await self.close_reservation_in_transaction(
                    job_id, conservative_output_tokens=config.work_output_tokens if config is not None else 0)
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
                await self._validate_job_initiator_in_transaction(proposal, scene_id, sources)
                if proposal.reused_work is not None:
                    original=await self.get_job(proposal.reused_work.job_id,scene_id)
                    if original is None or ReusedWorkResult.from_job(original)!=proposal.reused_work:
                        raise JobChanged('复用的工作成果已变化或不在本场景；须重新读取并选择原结果版本')
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

    async def _validate_job_initiator_in_transaction(self, proposal, scene_id, sources):
        """Verify the stated branch against its real stored event.

        The three branches are validated separately.  A system or plugin
        origin is never accepted through the human branch, and no branch may
        pass by leaving its identity empty.
        """
        initiator = proposal.initiator
        if initiator is None:
            raise ValueError('Work creation needs an explicit initiator')
        row = await (await self._db.execute(
            'SELECT event_type,actor_id FROM events WHERE id=? AND scene_id=?',
            (proposal.request_source_event_id, scene_id))).fetchone()
        if row is None or proposal.request_source_event_id not in sources:
            raise ValueError('Work request anchor must be a real event of this scene')
        event_type, actor_id = row
        if initiator.principal_type == 'human':
            if event_type not in {'GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED'}:
                raise ValueError('Work requester must match its explicit human request source')
            if actor_id != 'user:' + initiator.user_id or initiator.user_id != proposal.requester_qq_uid:
                raise ValueError('Work requester must match its explicit human request source')
            if proposal.request_source_event_id != initiator.request_event_id:
                raise ValueError('Human initiator must be the same real request source as its request anchor')
            return
        if proposal.requester_qq_uid is not None:
            raise ValueError('Only a human initiator may carry a QQ requester')
        if initiator.principal_type == 'plugin':
            if initiator.source_event_id != proposal.request_source_event_id:
                raise ValueError('Plugin initiator must be the same real request source as its request anchor')
            if actor_id != 'plugin:' + initiator.plugin_id:
                raise ValueError('Plugin work must name the plugin that actually produced its source event')
            return
        if initiator.trigger_event_id != proposal.request_source_event_id:
            raise ValueError('System initiator must be the same real request source as its request anchor')
        if not actor_id.startswith('system:'):
            raise ValueError('System work must name a real system-produced source event, not a group message')
        if initiator.agent_id != actor_id.removeprefix('system:'):
            raise ValueError('System initiator must be the agent that actually produced its source event')

    @staticmethod
    def initiator_of(job):
        """The typed initiator a job record already carries, or None.

        `_decode_job` has already converted an older record from its own exact
        requester and request anchor, so this reads the same stored identity
        the work was created under instead of rebuilding one at the control
        site.  A record without a definite anchor keeps None.
        """
        stored = job.get('initiator')
        if stored is None:
            return None
        from pydantic import TypeAdapter
        from len_bot.events.models import Initiator
        return TypeAdapter(Initiator).validate_python(stored)

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
                            or existing['initiator'] != (proposal.initiator.model_dump(mode='json') if proposal.initiator else None)
                            or existing['goal'] != proposal.goal
                            or existing['work_operation'] != proposal.work_operation
                            or existing['work_parameters'] != parameters
                            or existing['reused_work'] != (proposal.reused_work.model_dump(mode='json') if proposal.reused_work else None)
                            or existing['plugin_origin'] != (proposal.plugin_origin.model_dump() if proposal.plugin_origin else None)
                            or existing['constraints'] != list(dict.fromkeys(proposal.constraints_add))
                            or existing['status'] not in {'pending', 'claimed', 'processing'}):
                        raise ValueError('Job creation reference does not match this unchanged human request')
                    if existing['ack_action_id']:
                        raise ValueError('This work already has a committed confirmation; inspect its existing action')
                    continue
                progress=await self._new_work_progress(scene_id,proposal)
                from len_bot.runtime.heartbeat import admit_cycle_in_transaction
                public_research = await admit_cycle_in_transaction(self, proposal, scene_id, job_id)
                payload = {"kind": "agent_job", "proposal_id": proposal.proposal_id, "source_event_ids": sources,
                           'public_research': public_research,
                           "work_operation":proposal.work_operation, "requester_qq_uid":proposal.requester_qq_uid,
                           "request_source_event_id":proposal.request_source_event_id,
                           "initiator":proposal.initiator.model_dump(mode='json') if proposal.initiator else None,
                           "observation_reads":{},
                           'reused_work':proposal.reused_work.model_dump(mode='json') if proposal.reused_work else None,
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
                # The ceiling this work is granted and the execution limits it
                # will run under are written here, in the same transaction as
                # the hold: a later revision or resume reads these rather than
                # today's defaults, and a work whose result has been settled
                # still knows what it was originally allowed to spend.
                tokens = await self.reserve_job_budget_in_transaction(
                    job_id, scene_id, proposal.initiator, work_operation=proposal.work_operation)
                snapshot = self.budget_snapshot(token_limit=tokens)
                if public_research:
                    seconds = 1800 if snapshot.job_max_seconds is None else min(1800, snapshot.job_max_seconds)
                    snapshot = snapshot.model_copy(update={'job_max_seconds': seconds,
                        'deadline_at': self.clock() + seconds})
                await self._db.execute("UPDATE agent_jobs SET budget_json=? WHERE id=? AND scene_id=?",
                    (snapshot.model_dump_json(), job_id, scene_id))
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
            if proposal.operation == 'cancel':
                # A cancelled work still owes whatever it already spent; only
                # the unused part of its hold goes back.  Its own typed
                # initiator is already the one its hold was opened under, so
                # the account cannot be swapped by a control.
                config = getattr(self, 'budget_config', None)
                await self.close_reservation_in_transaction(
                    job_id, conservative_output_tokens=config.work_output_tokens if config is not None else 0)
            elif proposal.operation in {'resume', 'revise'}:
                # A resume or a revise makes the work executable again — the
                # next execution is a new run under the same identity — so it
                # needs a live hold again.  Without one its token dimension
                # would read as "no ceiling" instead of the ceiling it was
                # created under, and the work would run with no account at all
                # while its counts stay unlimited by default.  Neither
                # operation extends that ceiling: the work's cumulative spend
                # keeps counting against the same number, on the day it was
                # accepted.  A work older than the reservation regime has no
                # hold, so it keeps no token dimension rather than being given
                # an invented one; a work whose hold exists but whose identity
                # was never definite is refused by the re-hold itself instead
                # of being billed to a guessed account.
                if await self.job_reservation(job_id) is not None:
                    await self.rehold_job_budget_in_transaction(
                        job_id, self.initiator_of(current), scene_id,
                        work_operation=current['work_operation'])
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
                page_refs={reference:(ident,unit,*span) for ident,units in reads.items()
                    for unit,record in units.items() for reference,span in record.get('evidence_refs',{}).items()}
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
                    if presentation.evidence_ref:
                        if presentation.start>=presentation.end:
                            raise ValueError('A page reference needs a nonempty actual presentation')
                        key=(presentation.result_id,presentation.coordinate_unit,presentation.start,presentation.end)
                        previous=page_refs.get(presentation.evidence_ref)
                        if previous is not None and previous!=key:
                            raise ValueError('A page reference cannot change its stored observation or range')
                        recorded.setdefault('evidence_refs',{})[presentation.evidence_ref]=[presentation.start,presentation.end]
                        page_refs[presentation.evidence_ref]=key
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
                    # A count the operator left unlimited is not a bound; only
                    # the dimensions that actually carry a number can refuse.
                    steps_limit, calls_limit, seconds_limit = limits
                    if steps_limit is not None and steps > steps_limit:
                        raise JobBudgetExhausted('Work model-step budget exhausted')
                    if calls_limit is not None and calls > calls_limit:
                        raise JobBudgetExhausted('Work tool-call budget exhausted',budget_kind='tool_calls')
                    if seconds_limit is not None and (elapsed > seconds_limit or model_steps and elapsed >= seconds_limit):
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

    async def complete_job(self, job_id, scene_id, revision, result: JobResult, *, work_state=None, skill_candidate=None, bot_actor_id=''):
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
                            if segment.type in {'image', 'video', 'audio'} and await self.get_media(segment.asset_id,[scene_id]) is None:
                                raise ValueError('Prepared work media is not registered in this scene')
                    await self._validate_evidence_spans(job, result.evidence_spans, result.result_ids)
                    if result.public_interests:
                        if result.status not in {'completed', 'partial'}:
                            raise ValueError('未形成成果的工作不能采用公共兴趣')
                        from len_bot.memory.interests import InterestStore
                        await InterestStore(self).adopt_in_transaction(job, result.public_interests)
                    if result.work_state is not None:
                        await self._validate_work_state(job, result.work_state)
                    if work_state is not None:
                        if work_state.goal_revision != revision:
                            raise ValueError('Work state belongs to an obsolete goal')
                        await self._validate_work_state(job, work_state)
                        await self._db.execute("UPDATE agent_jobs SET work_state_json=? WHERE id=? AND scene_id=?", (work_state.model_dump_json(), job_id, scene_id))
                    if skill_candidate is not None:
                        if job.get('public_research'):
                            raise ValueError('公共研究不从此入口生成场景技能')
                        if spec and not spec.allow_learning:
                            raise ValueError('This plugin work does not create procedural skills')
                        await self.add_skill_candidate_in_transaction(job, skill_candidate, bot_actor_id=bot_actor_id)
                except ValueError as error:
                    raise JobResultRejected(str(error)) from error
                await self._db.execute("UPDATE agent_jobs SET result_json=?,updated_at=? WHERE id=? AND scene_id=?",
                    (result.model_dump_json(), self.clock(), job_id, scene_id))
                from len_bot.runtime.public_research import has_public_context
                result_status = ('completed' if result.status in {'completed', 'partial'} else 'failed') if has_public_context(job) else 'result_ready'
                await self._db.execute("UPDATE tasks SET status=?,payload=json_set(payload,'$.result',?) WHERE id=? AND scene_id=?",
                    (result_status, result.summary, job_id, scene_id))
                event = await self._queue_job_event(EventType.AGENT_JOB_FINISHED, job_id, scene_id, revision,
                    {"raw_text": "信息工作已有结果，结合最新要求核对后决定如何回应。", "result": result.model_dump(), "origin_mode": job["origin_mode"]})
                # A skill candidate committed above is admitted consumption of
                # this work's original hold.  The close below reads the
                # persisted candidates — not just this call's argument — so a
                # candidate saved earlier through update_work_state keeps the
                # ceiling occupied the same way; otherwise unused remainder
                # returns to the day now.
                config = getattr(self, 'budget_config', None)
                await self.close_reservation_in_transaction(
                    job_id, conservative_output_tokens=config.work_output_tokens if config is not None else 0)
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

    async def update_work_state(self, job_id, scene_id, revision, state: WorkState, skill_candidate=None, *, bot_actor_id=''):
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
                    from len_bot.runtime.public_research import has_public_context
                    if has_public_context(job):
                        raise ValueError('公共研究不读写场景技能候选')
                    spec=self.plugin_work(job)
                    if spec and not spec.allow_learning:
                        raise ValueError('This plugin work does not create procedural skills')
                    await self.add_skill_candidate_in_transaction(job, skill_candidate, bot_actor_id=bot_actor_id)
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
                    steps_limit, _calls_limit, seconds_limit = limits
                    if steps_limit is not None and steps > steps_limit:
                        raise JobBudgetExhausted('Work model-step budget exhausted before skill maintenance')
                    if seconds_limit is not None and (elapsed > seconds_limit or model_steps and elapsed >= seconds_limit):
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
