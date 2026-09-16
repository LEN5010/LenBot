"""Deferred sends use the existing task and event transaction, not a second queue."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from len_bot.actions.models import ActionItem
from len_bot.events.models import EventType
from len_bot.scheduler.models import TaskItem


class DeferredDelivery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['deferred_delivery'] = 'deferred_delivery'
    action_id: str
    action: dict
    detail: str
    original_due_at: float | None = None
    delivery_phase: Literal['waiting', 'queued', 'attempted', 'terminal'] = 'waiting'
    claim_run_id: str | None = None
    delivery_status: str | None = None
    delivery_event_id: str | None = None
    error: str | None = None


def stored_action(action: ActionItem) -> dict:
    return action.model_dump(mode='json', exclude={'content', 'resolved_images', 'resolved_sticker_ids'})


class DeliveryStoreMixin:
    async def delivery_fact(self, action_id, scene_id):
        row = await (await self._db.execute("""SELECT id,event_type,payload FROM events
            WHERE scene_id=? AND json_extract(payload,'$.action_id')=?
              AND event_type IN ('MESSAGE_SENT','MESSAGE_SEND_FAILED','ACTION_SHADOWED')
            ORDER BY rowid DESC LIMIT 1""", (scene_id, action_id))).fetchone()
        if row:
            data = json.loads(row[2])
            status = ('sent' if row[1] == 'MESSAGE_SENT' else 'shadow' if row[1] == 'ACTION_SHADOWED'
                      else 'unknown' if data.get('delivery_unknown') else data.get('delivery_status', 'not_sent'))
            return status, row[0], data.get('error', '')
        attempted = await (await self._db.execute('SELECT 1 FROM events WHERE id=? AND scene_id=?',
            ('send-attempt:' + action_id, scene_id))).fetchone()
        return ('unknown', None, '发送尝试已登记，但没有可靠终态回执') if attempted else None

    async def defer_delivery(self, action, due_at, detail):
        async with self._write_lock:
            try:
                if await self.delivery_fact(action.id, action.scene_id):
                    return None
                row = await (await self._db.execute("""SELECT id,payload,status FROM tasks
                    WHERE scene_id=? AND json_extract(payload,'$.kind')='deferred_delivery'
                      AND json_extract(payload,'$.action_id')=? ORDER BY created_at DESC LIMIT 1""",
                    (action.scene_id, action.id))).fetchone()
                ident = row[0] if row else 'dd:' + action.id
                if row and row[2] not in {'pending', 'claimed', 'processing'}:
                    return None
                if row:
                    payload = DeferredDelivery.model_validate_json(row[1])
                    payload.delivery_phase, payload.claim_run_id = 'waiting', None
                    payload.detail = detail
                else:
                    original_due = action.planned_at
                    if action.fulfils_task_id:
                        original = await (await self._db.execute('SELECT due_at FROM tasks WHERE id=? AND scene_id=?',
                            (action.fulfils_task_id, action.scene_id))).fetchone()
                        if original:
                            original_due = original[0]
                    action = action.model_copy(update={'deferred_task_id': ident})
                    payload = DeferredDelivery(action_id=action.id, action=stored_action(action),
                        original_due_at=original_due, detail=detail)
                if row:
                    await self._db.execute("""UPDATE tasks SET status='pending',due_at=?,payload=?,trigger_event_id=NULL
                        WHERE id=? AND scene_id=?""", (due_at, payload.model_dump_json(), ident, action.scene_id))
                else:
                    await self._db.execute("""INSERT INTO tasks
                        (id,scene_id,description,due_at,status,source_event_id,payload,created_at,wake_event_type,origin_mode)
                        VALUES (?,?,?,?,'pending',?,?,?,?,'system')""",
                        (ident, action.scene_id, '睡眠延期交付', due_at, action.origin_event_id or 'system:sleep',
                         payload.model_dump_json(), self.clock(), EventType.SCENE_WAKE_CONFIRMED.value))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return await self.get_task(ident)

    async def claim_deferred_delivery(self, task_id, scene_id, run_id):
        async with self._write_lock:
            try:
                row = await (await self._db.execute('SELECT payload,status FROM tasks WHERE id=? AND scene_id=?',
                    (task_id, scene_id))).fetchone()
                if not row or row[1] not in {'claimed', 'processing'}:
                    return None
                payload = DeferredDelivery.model_validate_json(row[0])
                if payload.delivery_phase != 'waiting' or await self.delivery_fact(payload.action_id, scene_id):
                    return None
                payload.delivery_phase, payload.claim_run_id = 'queued', run_id
                await self._db.execute("UPDATE tasks SET status='processing',payload=? WHERE id=? AND scene_id=?",
                    (payload.model_dump_json(), task_id, scene_id))
                await self._db.commit()
                action = ActionItem.model_validate(payload.action)
                return action.model_copy(update={'original_due_at': payload.original_due_at,
                    'delivery_late_seconds': max(0, self.clock() - payload.original_due_at)
                        if payload.original_due_at is not None else None})
            except BaseException:
                await self._db.rollback()
                raise

    async def begin_delivery_attempt(self, action) -> bool:
        """Persist before the adapter; a lost response must never cause a replay."""
        async with self._write_lock:
            try:
                if await self.delivery_fact(action.id, action.scene_id):
                    return False
                if action.interest_publication:
                    from len_bot.runtime.interest_publication import check_publication
                    await check_publication(self, action.scene_id, action.interest_publication, action_id=action.id)
                await self._db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                    ('send-attempt:' + action.id, EventType.DELIVERY_ATTEMPTED.value, action.scene_id,
                     'system:action_queue', self.clock(), json.dumps({'action_id': action.id,
                     'deferred_task_id': action.deferred_task_id, 'fulfils_task_id': action.fulfils_task_id,
                     'interest_publication': action.interest_publication.model_dump() if action.interest_publication else None}),
                     '{"conversation_excluded":true}'))
                if action.deferred_task_id:
                    changed = await self._db.execute("""UPDATE tasks SET payload=json_set(payload,'$.delivery_phase','attempted')
                        WHERE id=? AND scene_id=? AND status='processing'
                        AND json_extract(payload,'$.delivery_phase')='queued'""",
                        (action.deferred_task_id, action.scene_id))
                    if changed.rowcount != 1:
                        raise ValueError('延期发送不再拥有当前领取')
                await self._db.commit()
                return True
            except BaseException:
                await self._db.rollback()
                raise

    async def finish_deferred_in_transaction(self, event):
        if event.event_type not in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}:
            return
        status = ('sent' if event.event_type == EventType.MESSAGE_SENT else
                  'shadow' if event.event_type == EventType.ACTION_SHADOWED else
                  'unknown' if event.payload.get('delivery_unknown') else event.payload.get('delivery_status', 'not_sent'))
        task_status = {'sent': 'completed', 'shadow': 'shadow_observed', 'unknown': 'delivery_unknown'}.get(status, 'failed')
        if event.payload.get('cancelled'):
            task_status = 'cancelled'
        await self._db.execute("""UPDATE tasks SET status=?,payload=json_set(payload,
            '$.delivery_phase','terminal','$.delivery_status',?,'$.delivery_event_id',?,'$.error',?)
            WHERE scene_id=? AND json_extract(payload,'$.kind')='deferred_delivery'
              AND json_extract(payload,'$.action_id')=? AND status IN ('pending','claimed','processing')""",
            (task_status, status, event.id, event.payload.get('error', ''), event.scene_id, event.payload.get('action_id')))

    async def recover_deferred_delivery_tasks(self):
        """Only explicit unattempted phases are recoverable. Old claims stay unknown."""
        async with self._write_lock:
            try:
                rows = await (await self._db.execute("""SELECT id,scene_id,payload,status FROM tasks
                    WHERE json_extract(payload,'$.kind')='deferred_delivery'
                    AND status IN ('pending','claimed','processing') ORDER BY created_at DESC""")).fetchall()
                seen = set()
                for ident, scene, raw, old_status in rows:
                    data = json.loads(raw)
                    action_data = dict(data['action'])
                    action_data.pop('content', None)  # Older ActionItem included its computed display field.
                    action_data['deferred_task_id'] = ident
                    action = ActionItem.model_validate(action_data)
                    key = scene, action.id
                    fact = await self.delivery_fact(action.id, scene)
                    legacy_unknown = 'delivery_phase' not in data and old_status != 'pending'
                    payload = DeferredDelivery(action_id=action.id, action=stored_action(action),
                        original_due_at=data.get('original_due_at') if 'delivery_phase' in data else action.planned_at,
                        detail=data.get('detail', '旧延期记录'))
                    if key in seen:
                        status, payload.delivery_phase, payload.error = 'cancelled', 'terminal', '被同一行动的较新延期意图替代'
                    elif fact or legacy_unknown or data.get('delivery_phase') == 'attempted':
                        result, receipt, error = fact or ('unknown', None, '旧领取缺少未尝试证据，需人工核对')
                        status = {'sent': 'completed', 'shadow': 'shadow_observed', 'unknown': 'delivery_unknown'}.get(result, 'failed')
                        payload.delivery_phase, payload.delivery_status = 'terminal', result
                        payload.delivery_event_id, payload.error = receipt, error
                        await self._db.execute("""UPDATE tasks SET status=? WHERE scene_id=? AND status='awaiting_delivery'
                            AND json_extract(payload,'$.delivery_action_id')=?""", (status, scene, action.id))
                    else:
                        status = 'pending'
                    seen.add(key)
                    await self._db.execute('UPDATE tasks SET status=?,payload=?,trigger_event_id=NULL WHERE id=? AND scene_id=?',
                        (status, payload.model_dump_json(), ident, scene))
                    await self._db.execute("DELETE FROM pending_runtime_events WHERE json_extract(event_json,'$.payload.task_id')=? AND scene_id=?", (ident, scene))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
