"""Scheduler-owned public research cycles; missed slots and unknown stops never replay."""
from __future__ import annotations

import json

from len_bot.events.models import EventType, SystemInitiator
from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.models import EpisodeOutcome
from len_bot.runtime.sleep_policy import in_sleep_window
from len_bot.scheduler.models import TaskItem, TaskStatus

HEARTBEAT_SCENE = 'system:heartbeat'
SLOT_SECONDS = 30 * 60
PLAN_VERSION = 'c18'


def slot_id(now):
    return int(now // SLOT_SECONDS)


def task_id_for(agent_id, slot):
    return f'hb:{agent_id}:{PLAN_VERSION}:{slot}'


def occupancy_id(agent_id, slot):
    return f'hb:run:{agent_id}:{PLAN_VERSION}:{slot}'


async def admit_cycle_in_transaction(store, proposal, scene_id, job_id):
    """The Gate's existing work transaction binds slot, occupancy, work and hold."""
    initiator = proposal.initiator
    if initiator.principal_type != 'system' or initiator.purpose != 'heartbeat':
        return None
    if scene_id != HEARTBEAT_SCENE or initiator.agent_id != 'scheduler' or proposal.source_event_ids != [initiator.trigger_event_id]:
        raise ValueError('心跳只能由本系统 Scheduler 的唯一真实槽事件建立')
    row = await (await store._db.execute('SELECT actor_id,event_type,payload FROM events WHERE id=? AND scene_id=?',
        (initiator.trigger_event_id, scene_id))).fetchone()
    if not row or row[0] != 'system:scheduler' or row[1] != 'TASK_DUE':
        raise ValueError('心跳没有 Scheduler 来源')
    data = json.loads(row[2])
    slot = data.get('payload') or {}
    if slot.get('kind') != 'heartbeat' or data.get('task_id') != initiator.cycle_id:
        raise ValueError('心跳来源和周期不一致')
    task = await store.get_task(initiator.cycle_id)
    if task is None or task.status != TaskStatus.PROCESSING or task.trigger_event_id != initiator.trigger_event_id:
        raise ValueError('心跳槽没有当前有效领取')
    occupied = await (await store._db.execute("""SELECT 1 FROM tasks WHERE scene_id=?
        AND json_extract(payload,'$.kind')='heartbeat_occupancy'
        AND status IN ('pending','claimed','processing','review_required','delivery_unknown') LIMIT 1""", (scene_id,))).fetchone()
    if occupied:
        raise ValueError('已有心跳工作或未确认终止仍占用本轮')
    active = await (await store._db.execute("SELECT COUNT(*) FROM tasks WHERE json_extract(payload,'$.kind')='agent_job' AND status IN ('pending','claimed','processing')")).fetchone()
    authority = store.capability_authority
    if authority is None:
        raise ValueError('心跳公共研究缺少当前能力授权')
    root = authority.config_store.current
    if not root.runtime.heartbeat_enabled or not root.runtime.jobs_enabled:
        raise ValueError('心跳或后台工作已关闭，不采用待建立的公共研究')
    now = store.clock()
    if int(slot['slot']) != slot_id(now):
        raise ValueError('错过的心跳槽不能创建工作')
    if root.runtime.job_max_concurrent < 2 or active[0] >= root.runtime.job_max_concurrent - 1:
        raise ValueError('工作容量不足，至少保留一个人类工作位置')
    if in_sleep_window(root.time, now):
        raise ValueError('当前已进入睡眠，不采用待建立的公共研究')
    from len_bot.runtime.capabilities import Capability, subject_for
    permission = authority.check(Capability.PUBLIC_RESEARCH, subject_for(initiator, scene_id), now=now)
    if not permission.allowed:
        raise ValueError(permission.reason)
    ident = occupancy_id(slot['agent_id'], slot['slot'])
    origin = {'seed_event_id': initiator.trigger_event_id, 'cycle_id': initiator.cycle_id, 'occupancy_id': ident}
    payload = {'kind': 'heartbeat_occupancy', 'slot': slot['slot'], 'job_id': job_id,
               'job_revision': 1, 'source_event_id': initiator.trigger_event_id}
    await store._db.execute("""INSERT INTO tasks(id,scene_id,description,due_at,status,source_event_id,payload,created_at,origin_mode)
        VALUES(?,?,?,?,'processing',?,?,?,'system')""", (ident, scene_id, '公共研究周期占用', store.clock(),
        initiator.trigger_event_id, json.dumps(payload), store.clock()))
    await store._db.execute("UPDATE tasks SET status='completed',payload=json_set(payload,'$.job_id',?,'$.outcome','work_created') WHERE id=? AND scene_id=?",
        (job_id, initiator.cycle_id, scene_id))
    return origin


class Heartbeat:
    def __init__(self, runtime):
        self.runtime = runtime

    def enabled(self):
        return bool(self.runtime.config.heartbeat_enabled and self.runtime.config.jobs_enabled)

    def _agent_id(self):
        return str(self.runtime.config.bot_qq)

    async def ensure_next(self, now=None):
        if not self.enabled():
            return
        store = self.runtime.event_store
        now = store.clock() if now is None else now
        nxt = slot_id(now) + 1
        ident = task_id_for(self._agent_id(), nxt)
        if await store.has_task(ident):
            return
        from len_bot.memory.interests import InterestStore
        interests = await InterestStore(store).list_public(limit=4)
        task = TaskItem(id=ident, scene_id=HEARTBEAT_SCENE, description=f'心跳槽 {nxt}',
            due_at=nxt * SLOT_SECONDS, payload={'kind': 'heartbeat', 'slot': nxt, 'plan_version': PLAN_VERSION,
            'agent_id': self._agent_id(), 'seed': {'topics': list(self.runtime.config.heartbeat_topics),
                'interests': [item.model_dump(mode='json') for item in interests]}},
            source_event_id='system:heartbeat', origin_mode='system')
        await store.save_task(task)
        self.runtime.scheduler.schedule_task(task)

    async def owns_event(self, event):
        if event.scene_id != HEARTBEAT_SCENE or event.actor_id != 'system:scheduler' or event.event_type != EventType.TASK_DUE:
            return False
        payload = event.payload.get('payload') or {}
        if payload.get('kind') != 'heartbeat' or payload.get('agent_id') != self._agent_id() or payload.get('plan_version') != PLAN_VERSION:
            return False
        task = await self.runtime.event_store.get_task(event.payload.get('task_id'))
        return bool(task and task.scene_id == HEARTBEAT_SCENE and task.status == TaskStatus.PROCESSING
            and task.trigger_event_id == event.id and task.id == task_id_for(self._agent_id(), payload.get('slot'))
            and not event.metadata.get('obsolete_task_wake'))

    async def run_slot(self, event):
        if not await self.owns_event(event):
            return
        store = self.runtime.event_store
        now = store.clock()
        payload = event.payload['payload']
        outcome, detail = 'completed_empty', ''
        try:
            if payload['slot'] != slot_id(now):
                outcome = 'missed'
            elif not self.enabled():
                outcome = 'skipped_disabled'
            elif in_sleep_window(self.runtime.config_store.current.time, now):
                outcome = 'skipped_sleep'
            elif await self.occupied():
                outcome = 'skipped_busy'
            else:
                seed = payload.get('seed') or {}
                if seed.get('topics') or seed.get('interests'):
                    initiator = SystemInitiator(agent_id='scheduler', trigger_event_id=event.id,
                        purpose='heartbeat', cycle_id=event.payload['task_id'])
                    from len_bot.runtime.capabilities import Capability, subject_for
                    permission = self.runtime.runtime_gate.capability_authority.check(Capability.PUBLIC_RESEARCH,
                        subject_for(initiator, HEARTBEAT_SCENE), now=now)
                    if not permission.allowed:
                        outcome, detail = 'skipped_permission', permission.reason
                    else:
                        proposal = JobProposal(proposal_id='heartbeat_research',
                            goal='按以下公共主题与兴趣做一轮有限研究；没有有效新资料可以零成果结束。\n' + json.dumps(seed, ensure_ascii=False),
                            constraints_add=['只读匿名公开资料，不继承群史、成员资料、凭据或私有技能',
                                '最多 1800 秒，保留真实证据范围；本轮只保存候选，不发布群消息'],
                            source_event_ids=[event.id], request_source_event_id=event.id, initiator=initiator)
                        # The event callback must not await its own Actor queue.
                        self.runtime._spawn_background_task(self._create_work(event, proposal))
                        return
            await self._finish_slot(event, outcome, detail)
        finally:
            await self.ensure_next(now)

    async def _create_work(self, event, proposal):
        try:
            decision = await self.runtime.operator_outcome(HEARTBEAT_SCENE,
                EpisodeOutcome(decision_reason='Scheduler 心跳建立公共研究', job_proposals=[proposal]), source_event_ids=[event.id])
            if not decision.accepted:
                await self._finish_slot(event, 'skipped_admission', decision.reason)
        except Exception as error:
            await self._finish_slot(event, 'failed_admission', f'{type(error).__name__}: {error}')

    async def _finish_slot(self, event, outcome, detail=''):
        store = self.runtime.event_store
        async with store._write_lock:
            try:
                await store._db.execute("""UPDATE tasks SET status='completed',payload=json_set(payload,'$.outcome',?,'$.detail',?)
                    WHERE id=? AND scene_id=? AND status='processing'""", (outcome, detail, event.payload['task_id'], HEARTBEAT_SCENE))
                await store._db.commit()
            except BaseException:
                await store._db.rollback()
                raise
        await store.save_trace(kind='heartbeat', scene_id=HEARTBEAT_SCENE, ref_id=event.payload['task_id'],
            payload={'slot': event.payload['payload']['slot'], 'outcome': outcome, 'detail': detail})

    async def occupied(self):
        store = self.runtime.event_store
        held = [task for task in await store.scene_tasks(HEARTBEAT_SCENE)
            if task['payload'].get('kind') == 'heartbeat_occupancy'
            and task['status'] in {'pending', 'claimed', 'processing', 'review_required', 'delivery_unknown'}]
        busy = False
        for task in held:
            job_id = task['payload'].get('job_id')
            job = await store.get_job(job_id, HEARTBEAT_SCENE) if job_id else None
            executions = await (await store._db.execute("""SELECT 1 FROM execution_runs WHERE job_id=?
                AND state IN ('accepted','starting','running','cancel_requested','termination_unconfirmed') LIMIT 1""", (job_id,))).fetchone()
            if not job or job['status'] in {'pending', 'claimed', 'processing'} or executions:
                busy = True
                continue
            await store.mark_task_status(task['id'], TaskStatus.COMPLETED)
        return busy
