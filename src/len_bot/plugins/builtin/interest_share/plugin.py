"""One candidate per configured scene/slot, expressed by the existing social core."""
from __future__ import annotations

import asyncio
import json

from len_bot.events.models import EventType
from len_bot.actions.models import receipt_delivery_status
from len_bot.plugins.base import BasePlugin
from len_bot.runtime.heartbeat import SLOT_SECONDS, slot_id
from len_bot.runtime.interest_publication import check_publication, publication_for, scene_permission
from len_bot.runtime.sleep_policy import is_asleep
from len_bot.scheduler.models import TaskItem, TaskStatus
from len_bot.tools.results import ToolResult, ObservationProvenance
from .config import Candidate


def task_id(scene_id, slot):
    return f'interest-share:{scene_id}:{slot}'


class InterestShare(BasePlugin):
    def __init__(self, context):
        super().__init__(context.manifest)
        self.context, self.config = context, context.config
        self._busy = set()
        self._schedule_lock = asyncio.Lock()

    async def on_load(self, context):
        context.register_handler(id='candidate', description='按本群语境决定是否分享公共兴趣',
            match=lambda call: call.event.payload['plugin_id'] == self.manifest.id
                and call.event.payload['name'] == 'candidate',
            handler=self.on_candidate, event_types=(EventType.PLUGIN_EVENT,),
            sources=('plugin_event',), consume=True, validate=self.validate_candidate)
        context.register_hook('before_commit', id='single_publication', handler=self.before_commit)

    async def on_enable(self):
        await self.ensure_next()

    async def on_disable(self):
        for task in await self.context.event_store.get_pending_tasks():
            if task.payload.get('kind') == 'interest_share':
                await self.context._runtime.scheduler.cancel_task(task.id)

    def source_status(self):
        return {'scope': '匿名公共兴趣；各群独立判断与授权', 'slot_seconds': SLOT_SECONDS,
                'busy_scenes': sorted(self._busy), 'billing_purpose': 'interest_share',
                'permission': 'plugin:interest_share + 目标 scene_id + interest_share',
                'delivery': '实际尝试时占用本群当日次数；未知结果不重发，Shadow 不记作送达'}

    async def ensure_next(self):
        async with self._schedule_lock:
            if self.manifest.enabled:
                await self._ensure_next()

    async def _ensure_next(self):
        runtime, store = self.context._runtime, self.context.event_store
        slot = slot_id(self.context.now()) + 1
        for scene, _ in self.context.scene_configs():
            ident = task_id(scene, slot)
            if await store.has_task(ident):
                continue
            task = TaskItem(id=ident, scene_id=scene, description='公共兴趣分享机会',
                due_at=slot * SLOT_SECONDS, source_event_id='system:interest_share', origin_mode='system',
                payload={'kind': 'interest_share', 'slot': slot, 'plugin_id': self.manifest.id})
            await store.save_task(task)
            runtime.scheduler.schedule_task(task)

    async def run_slot(self, event):
        store = self.context.event_store
        task = await store.get_task(event.payload['task_id'])
        payload = event.payload['payload']
        if (event.actor_id != 'system:scheduler' or task is None or task.status != TaskStatus.PROCESSING
                or task.trigger_event_id != event.id or task.scene_id != event.scene_id
                or task.id != task_id(event.scene_id, payload['slot'])
                or event.metadata.get('obsolete_task_wake')):
            return
        outcome, detail = 'no_candidate', ''
        try:
            if payload['slot'] != slot_id(self.context.now()):
                outcome = 'missed'
                return
            if event.scene_id in self._busy:
                outcome = 'skipped_busy'
                return
            config, _ = scene_permission(store, event.scene_id)
            runtime = self.context._runtime
            if await runtime.rate_limiter.exhausted(event.scene_id):
                outcome = 'skipped_hourly_limit'
                return
            actor = await runtime.scene_manager.get_or_create_actor(event.scene_id)
            if is_asleep(actor.session, self.context.time_settings, self.context.now()):
                outcome = 'skipped_sleep'
                return
            from len_bot.runtime.gate import MAX_CONSECUTIVE_BOT_MESSAGES
            if actor.session.consecutive_bot_messages >= MAX_CONSECUTIVE_BOT_MESSAGES:
                outcome = 'skipped_anti_loop'
                return
            candidates = await runtime.interest_store.list_public(limit=20, considered_in_scene=event.scene_id,
                topics=config.topics, publishable_only=True)
            for item in candidates:
                _, publication = await publication_for(store, item.id, item.revision)
                try:
                    await check_publication(store, event.scene_id, publication)
                except ValueError as error:
                    detail = str(error)
                    continue
                candidate = Candidate(task_id=task.id, slot=payload['slot'], interest_id=item.id, revision=item.revision)
                await self.context.emit_event('candidate', candidate, scene_id=event.scene_id,
                    event_id='interest-candidate:' + task.id, timestamp=self.context.now())
                outcome = 'candidate_offered'
                break
        except ValueError as error:
            outcome, detail = 'skipped', str(error)
        except Exception as error:
            outcome, detail = 'failed', f'{type(error).__name__}: {error}'
            raise
        finally:
            await store.mark_task_status(task.id, TaskStatus.COMPLETED)
            await store.save_trace(kind='interest_share', scene_id=event.scene_id, ref_id=task.id,
                payload={'slot': payload['slot'], 'outcome': outcome, 'detail': detail})
            await self.ensure_next()

    async def validate_candidate(self, call):
        candidate = Candidate.model_validate(call.event.payload['data'])
        store = self.context.event_store
        task = await store.get_task(candidate.task_id)
        if (task is None or task.scene_id != call.scene_id or task.payload.get('kind') != 'interest_share'
                or task.id != task_id(call.scene_id, candidate.slot)
                or call.event.id != 'interest-candidate:' + task.id
                or task.trigger_event_id is None):
            raise ValueError('兴趣分享没有真实 Scheduler 槽来源')
        # Do not replay yesterday's wording after sleep or a process restart.
        if candidate.slot != slot_id(self.context.now()):
            raise ValueError('本次兴趣分享机会已过期')
        _, publication = await publication_for(store, candidate.interest_id, candidate.revision)
        await check_publication(store, call.scene_id, publication)
        actor = await self.context._runtime.scene_manager.get_or_create_actor(call.scene_id)
        if call.execution and is_asleep(actor.session, self.context.time_settings, self.context.now()):
            raise ValueError('本群当前睡眠，不开始主动社会判断')

    async def before_commit(self, view, call):
        if len(view.messages) > 1 or any(segment.type != 'text' for message in view.messages for segment in message):
            view.stop_reason = '本次兴趣分享只接受一条有来源的短文字，不附带成员提及或未取得的媒体'
        return view

    async def on_candidate(self, call):
        if call.scene_id in self._busy:
            return
        candidate = Candidate.model_validate(call.event.payload['data'])
        consideration = {'interest_id': candidate.interest_id, 'revision': candidate.revision,
            'slot': candidate.slot, 'context_cutoff_rowid': call.cutoff_rowid,
            'outcome': 'failed', 'reason': '',
            'reconsideration': '按本群最近考虑时间轮换；候选修订后重新取得优先机会，沉默不永久屏蔽'}
        self._busy.add(call.scene_id)
        try:
            item, publication = await publication_for(self.context.event_store, candidate.interest_id, candidate.revision)
            materials = ToolResult(content=json.dumps({'interest': item.model_dump(mode='json'),
                'resource_urls': publication.resource_urls,
                'meaning': '研究形成的公共候选，评价与事实已标注；不是本群成员原话。引用范围以 evidence_spans 为准。'}, ensure_ascii=False),
                coverage='公共兴趣及已验证的来源关系，不代表重新读取完整资源', evidence_kind='retrieval',
                provenance=ObservationProvenance(access='derived', source_result_ids=publication.source_result_ids))
            rows = await (await self.context.event_store._db.execute("""SELECT id,timestamp,payload,metadata FROM events
                WHERE scene_id=? AND rowid<=? AND event_type='MESSAGE_SENT'
                  AND actor_id=? AND json_extract(payload,'$.origin_mode')='live'
                  AND COALESCE(json_extract(payload,'$.delivery_status'),'sent')='sent'
                  AND COALESCE(json_extract(payload,'$.delivery_unknown'),0)=0
                  AND json_type(payload,'$.message_id') IN ('text','integer')
                  AND length(trim(CAST(json_extract(payload,'$.message_id') AS TEXT)))>0
                  AND COALESCE(json_extract(metadata,'$.simulated'),0)=0
                ORDER BY rowid DESC LIMIT 4""", (call.scene_id, call.cutoff_rowid, self.context._runtime.bot_actor_id))).fetchall()
            recent_deliveries = []
            for ident, at, raw, metadata in rows:
                payload = json.loads(raw)
                if receipt_delivery_status(EventType.MESSAGE_SENT, payload, json.loads(metadata)) == 'sent':
                    recent_deliveries.append({'event_id': ident, 'at': at, 'text_excerpt': payload.get('raw_text', '')[:300]})
            recent = ToolResult(content=json.dumps({'recent_deliveries': recent_deliveries,
                'meaning': '本群近期实际送达的 Bot 表达，每条只呈现前300字符，不是外部事实证据'}, ensure_ascii=False),
                evidence_kind='retrieval', coverage='本群最多四条真实送达记录的文字节选，每条前300字符',
                provenance=ObservationProvenance(access='scene', source_event_ids=[row['event_id'] for row in recent_deliveries]))
            result = await call.run_agent(instructions=(
                '这是本群本时段的一次自主分享机会，使用当前群的必要语境与近期已发内容判断相关性。'
                '不相关、已知、证据不足或会打扰时用 respond 保持沉默。研究完成不意味着应当发布。'
                '至多一条简短表达，区分公共事实与自己的评价，保留资源链接，不能把研究意向当成果。'
                '只引用本次提供的候选及证据范围，不声称重新看过视频或读过完整原文。'
                'source 使用本次候选事件，next 必须 end，不创建工作、提醒、群认识或关注窗口。'),
                input_observations=[materials, recent], tool_names=('read_tool_result',),
                model_role='conversation', include_identity=True, input_mode='conversation', output_mode='respond',
                max_steps=self.config.max_steps, max_tool_calls=2,
                context_tokens=self.config.context_tokens, output_tokens=self.config.output_tokens)
            consideration.update(outcome='expression_submitted' if result.message_proposals else 'silent',
                                 reason=result.decision_reason)
        except BaseException as error:
            consideration['error_type'] = type(error).__name__
            raise
        finally:
            self._busy.discard(call.scene_id)
            await self.context.event_store.save_trace(kind='interest_share_consideration',
                scene_id=call.scene_id, ref_id=candidate.task_id, payload=consideration)
