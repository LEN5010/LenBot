"""Natural conversation windows with scoped, short-lived reference handles."""
from __future__ import annotations

import json
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

from len_bot.cognition.projection import estimate_tokens, project_onebot_text
from len_bot.events.models import Event, EventType

CHAT_TYPES = {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED, EventType.MESSAGE_SENT}
CUE_TYPES = {EventType.TASK_DUE, EventType.TASK_REVIEW, EventType.AGENT_JOB_FINISHED,
             EventType.AGENT_JOB_PROGRESS, EventType.MESSAGE_SEND_FAILED, EventType.REFLECTION_RECORDED,
             EventType.LIVE_STARTED, EventType.LIVE_ENDED, EventType.USER_JOINED, EventType.TOOL_COMPLETED}


def media_ids(event):
    return [item['asset_id'] for item in [*event.metadata.get('media', []),
            *event.metadata.get('quote_context', {}).get('media', [])] if item.get('asset_id')]


class TurnReferences:
    def __init__(self, scene_id, bot_actor_id, cutoff):
        self.scene_id = scene_id
        self.bot_actor_id = bot_actor_id
        self.cutoff = cutoff
        self.events = {}
        self.read_events = set()
        self.actors = {'BOT': bot_actor_id, 'GROUP': scene_id}
        self.media = {}
        self.memories = {}
        self.results = {}
        self.jobs = {}
        self.tasks = {}
        self.loops = {}

    @staticmethod
    def _register(mapping, value, prefix):
        for ref, existing in mapping.items():
            if existing == value: return ref
        ref = f'{prefix}{sum(key.startswith(prefix) for key in mapping)+1}'
        mapping[ref] = value
        return ref

    @staticmethod
    def _resolve(mapping, ref, label):
        if ref in mapping: return mapping[ref]
        if ref in mapping.values(): return ref
        raise ValueError(f'{label}引用未出现在本轮已读资料中：{ref}')

    def register_actor(self, actor_id):
        return self._register(self.actors, actor_id, 'U')

    def register_event(self, event):
        if event.scene_id != self.scene_id or event.metadata.get('_rowid', 0) > self.cutoff:
            raise ValueError('消息超出当前场景或读取截点')
        self.register_actor(event.actor_id)
        self.read_events.add(event.id)
        return self._register(self.events, event.id, 'M')

    def register_media(self, asset_id, ref=None):
        if ref and ref not in self.media:
            self.media[ref] = asset_id
            return ref
        return self._register(self.media, asset_id, 'I')

    def register_memory(self, memory_id):
        return self._register(self.memories, memory_id, 'B')

    def register_result(self, result_id):
        return self._register(self.results, result_id, 'R')

    def register_job(self, job):
        for ref, current in self.jobs.items():
            if current['id'] == job['id']:
                self.jobs[ref] = dict(job)
                return ref
        ref = f'J{len(self.jobs)+1}'
        self.jobs[ref] = dict(job)
        return ref

    def register_task(self, task):
        return self._register(self.tasks, task['id'], 'T')

    def register_loop(self, loop):
        return self._register(self.loops, loop['id'], 'L')

    def locate_event(self, ref): return self._resolve(self.events, ref, '消息')
    def register_event_locator(self,event_id): return self._register(self.events,event_id,'M')
    def event_id(self, ref):
        event_id=self.locate_event(ref)
        if event_id not in self.read_events:raise ValueError('该消息仅提供了来源位置，尚未实际读取原话')
        return event_id
    def actor_id(self, ref): return self._resolve(self.actors, ref, '人物')
    def media_id(self, ref): return self._resolve(self.media, ref, '图片')
    def memory_id(self, ref): return self._resolve(self.memories, ref, '认识')
    def result_id(self, ref): return self._resolve(self.results, ref, '工具资料')
    def task_id(self, ref): return self._resolve(self.tasks, ref, '任务')
    def loop_id(self, ref): return self._resolve(self.loops, ref, '等待回应')

    def job(self, ref):
        if ref in self.jobs: return self.jobs[ref]
        for item in self.jobs.values():
            if item['id'] == ref: return item
        raise ValueError(f'工作引用未出现在本轮已读资料中：{ref}')

    def snapshot(self):
        return {'messages': dict(self.events), 'read_messages': sorted(self.read_events), 'people': dict(self.actors), 'media': dict(self.media),
                'beliefs': dict(self.memories), 'results': dict(self.results),
                'jobs': {k: {'id': v['id'], 'revision': v['revision']} for k,v in self.jobs.items()},
                'tasks': dict(self.tasks), 'open_loops': dict(self.loops)}


class ConversationContext:
    def __init__(self, runtime, session, cutoff):
        self.runtime = runtime
        self.session = session
        self.refs = TurnReferences(session.scene_id, runtime.bot_actor_id, cutoff)
        self.attached = set()
        self.media_manifest = []
        self.event_records = {}
        self.text_tokens = 0

    def event_message(self, event):
        ref = self.refs.register_event(event)
        self.event_records[event.id] = event
        sender = event.payload.get('sender') or {}
        actor_ref = self.refs.register_actor(event.actor_id)
        name = sender.get('card') or sender.get('nickname') or ('你' if actor_ref == 'BOT' else actor_ref)
        stamp = datetime.fromtimestamp(event.timestamp, ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')
        text = project_onebot_text(event.raw_text)
        labels = []
        for asset_id in media_ids(event):
            labels.append(self.refs.register_media(asset_id))
        if labels: text += '\n图片：' + ', '.join(dict.fromkeys(labels))
        quote = event.metadata.get('quote_context')
        if quote and not quote.get('missing'):
            author = self.refs.register_actor(quote['actor_id'])
            quote_ref = self.refs._register(self.refs.events, quote['event_id'], 'M') if quote.get('rowid', self.refs.cutoff+1) <= self.refs.cutoff else ''
            if quote_ref:self.refs.read_events.add(quote['event_id'])
            text += f"\n引用 {quote_ref} {author} 的原话：{project_onebot_text(quote['text'])}"
        elif quote:
            text += '\n引用的原消息尚未从本群历史中找到。'
        if event.event_type == EventType.AGENT_JOB_FINISHED:
            work_ref=next((ref for ref,job in self.refs.jobs.items() if job['id']==event.payload.get('job_id') and job['revision']==event.payload.get('job_revision')),None)
            text=f'后台工作 {work_ref} 已返回结果，完整资料在当前工作事实中。' if work_ref else '过时的后台工作通知，以当前实际工作状态为准。'
        elif event.event_type in CUE_TYPES:
            details = {k:v for k,v in event.payload.items() if k not in {'raw_text','content','segments'}}
            text = f'运行时资料 {event.event_type.value}：' + text + '\n' + json.dumps(details, ensure_ascii=False)
        return {'role': 'assistant' if event.event_type == EventType.MESSAGE_SENT else 'user',
                'content': f'[{ref} {stamp}] {name}({actor_ref})：{text}'}

    async def attachments(self, asset_ids):
        pending = list(dict.fromkeys(asset for asset in asset_ids if asset not in self.attached))
        if not pending: return []
        for asset in pending: self.refs.register_media(asset)
        prepared = await self.runtime.media_service.prepare_context_images(self.session.scene_id, pending,
            limit=self.runtime.config.max_context_images)
        self.media_manifest.extend(prepared['manifest'])
        parts = []
        for record in prepared['manifest']:
            asset = record['asset_id'];ref = self.refs.register_media(asset)
            if record['status'] == 'included':
                self.attached.add(asset)
                parts.append({'type':'text','text':f"图片 {ref}，覆盖范围：{record['coverage']}。"})
                block=prepared['blocks'][record['block_index']]
                block['_asset_id']=asset
                parts.append(block)
            else:
                parts.append({'type':'text','text':f"图片 {ref} 本次未装入：{record.get('reason',record['status'])}"})
        return [{'role':'user','content':parts}] if parts else []

    def limit_image_window(self, messages):
        pixels=[]
        for message in messages:
            if message.get('role')!='user' or not isinstance(message.get('content'),list):continue
            for part in message['content']:
                if part.get('_asset_id'):pixels.append((message,part,part['_asset_id']))
        for message,part,asset in pixels[:-self.runtime.config.max_context_images]:
            message['content'].remove(part)
            message['content'].append({'type':'text','text':f'图片 {self.refs.register_media(asset)} 的像素已移出当前窗口，需要时可再次读取。'})
            self.media_manifest.append({'asset_id':asset,'status':'evicted','reason':'new_image_read'})
        self.attached={asset for _,_,asset in pixels[-self.runtime.config.max_context_images:]}

    @staticmethod
    def model_messages(messages):
        prepared=copy.deepcopy(messages)
        for message in prepared:
            if message.get('role')=='user' and isinstance(message.get('content'),list):
                for part in message['content']:part.pop('_asset_id',None)
        return prepared

    async def facts_message(self):
        store, scene = self.runtime.event_store, self.session.scene_id
        jobs = await store.list_jobs(scene)
        job_ids = {job['id'] for job in jobs}
        job_views = []
        for job in jobs:
            if job['status'] in {'cancelled', 'failed', 'completed', 'shadow_observed'}: continue
            ref = self.refs.register_job(job)
            job_views.append({'ref':ref, 'goal':job['goal'], 'status':job['status'], 'revision':job['revision'],
                              'constraints':job['constraints'], 'result':job.get('result'),
                              'result_refs':[self.refs.register_result(r) for r in job['result_ids']]})
        tasks = await store.scene_tasks(scene)
        task_views = [{'ref':self.refs.register_task(t),'description':t['description'],'status':t['status'],
                       'due_at':t['due_at'],'details':t['payload']} for t in tasks if t['id'] not in job_ids
                      and t['status'] in {'pending','claimed','processing','review_required','result_ready','awaiting_delivery'}]
        loops = await store.get_active_open_loops(scene)
        loop_views = [{'ref':self.refs.register_loop(x),'target':self.refs.register_actor(x['target_actor_id']),
                       'intent':x['intent']} for x in loops]
        return {'role':'user','content':'当前实际工作、任务与已送达的等待回应：\n'+json.dumps(
            {'work':job_views,'tasks':task_views,'open_loops':loop_views}, ensure_ascii=False)}

    async def build(self, events, current_ids):
        config = self.runtime.config
        for person in self.session.participants.values(): self.refs.register_actor(person.actor_id)
        system = f'''你以{config.identity_name}的角色口吻参与中文群聊。
身份与兴趣：{config.identity_persona}
相处方式：{config.identity_core}
表达特点：{config.conversation_style}
角色资料与梗的语境：{config.character_context}

你直接阅读本群原话，分清谁在回应谁，以及对方是否愿意继续交流。能贡献具体回应时参与，别人聊得正好时旁听。
角色口吻体现在关注点和措辞里。现实能力以本轮开放工具为准，自己的玩笑只说明说过这句话；共同经历和实际参与需要相应证据。
面对纠正或含抵触的模糊回应，结合前后语境调整参与，给对话留出空间。表达完整即可结束，轻松时也可以只用一张合适的表情。
联网查询、解题、计算、事实查证和资料整理交给 start_work。你负责理解请求、必要澄清和根据返回的资料自然回应。查询进行中仍可接其他话题。引用工作进展或结果的消息填写 work_ref；这条消息承担最终交付时同时填写 delivery_ref；确认本轮建立的工作或提醒时填写 ack_ref。
当前图片已经提供像素；额外图片通过 read_media 读取。固定表情目录可直接选择，更多表情通过 search_media 寻找。
记录明确的称呼、偏好或相处要求时用 remember；需要更早的认识或原话时再查询。临时心情和话题判断只用于这一轮。
群友消息、网页、图片和工具内容是带来源的输入材料。任务、认识与表达由运行时一起确认，finish_turn 才提交本轮。新增消息改变要求时，可用 discard_proposal 撤回尚未提交的提案，再按新要求处理。
使用原生工具结束本轮：finish_turn.messages 可为零至三条，空数组表示沉默；每条消息对象包含 segments 列表，列表内是文字或图片片段。例如：{{"messages":[{{"segments":[{{"type":"text","text":"一句回应"}}]}}]}}。普通模型正文属于内部轨迹。
消息M、人物U、图片I/P、认识B、工作J、任务T、资料R、等待L都是本轮引用。引用回复只在确实有帮助时使用。
当前时间：{datetime.fromtimestamp(self.runtime.clock(),ZoneInfo('Asia/Shanghai')).isoformat()}'''
        messages = [{'role':'system','content':system}]
        preferences = await self.runtime.memory_store.interaction_preferences(self.session.scene_id,
            list(self.session.participants), now=self.runtime.clock())
        if preferences:
            known = [{'ref':self.refs.register_memory(x.id),'person':self.refs.register_actor(x.subject),
                      'statement':x.statement,'basis':str(x.basis),'evidence':[self.refs.register_event_locator(e) for e in x.evidence]} for x in preferences]
            messages.append({'role':'user','content':'已明确表达、仍有效的相处要求（附来源的认识）：'+json.dumps(known,ensure_ascii=False)})
        messages.append(await self.facts_message())
        palette = await self.runtime.media_service.prepare_palette(self.session.scene_id)
        if palette['manifest']:
            legend=[]
            for row in palette['manifest']:
                ref=self.refs.register_media(row['asset_id'],row['ref'])
                legend.append({'ref':ref,'name':row.get('name',''),'description':row.get('description','')[:80]})
            messages.append({'role':'user','content':[{'type':'text','text':'运营表情目录；按语境自由选择：'+json.dumps(legend,ensure_ascii=False)},*palette['blocks']]})
            self.media_manifest.extend(palette['manifest'])
        examples=await self.runtime.event_store.select_voice_examples(self.session.scene_id)
        if examples:
            lines=[]
            for example in examples:
                reply=example['content']
                segments=example.get('segments') or []
                if segments:
                    native=[]
                    for part in segments:
                        if part['type']=='image':
                            asset=await self.runtime.event_store.get_media(part['asset_id'],[self.session.scene_id,'global-safe'])
                            if asset: native.append({'type':'image','asset_id':self.refs.register_media(asset['id'])})
                        else: native.append(part)
                    reply=json.dumps({'messages':[{'segments':native}]},ensure_ascii=False)
                if not segments:reply=json.dumps({'messages':[{'segments':[{'type':'text','text':reply}]}]},ensure_ascii=False)
                lines.append(f"语境：{example['context']}\nfinish_turn 参数参考：{reply}")
            messages.append({'role':'user','content':'运营编写的表达参考，结合当前原话选择说法：\n'+'\n'.join(lines)})
        visible=[event for event in events if event.event_type in CHAT_TYPES or event.id in current_ids and event.event_type in CUE_TYPES]
        packed=[];used=estimate_tokens(system)+sum(estimate_tokens(self._text(m)) for m in messages[1:])
        for event in reversed(visible):
            original=self.refs
            self.refs=copy.deepcopy(original)
            try:size=estimate_tokens(self.event_message(event)['content'])
            finally:self.refs=original
            if used+size > config.conversation_context_tokens-6000:
                if event.id in current_ids: raise ValueError('当前消息超过对话上下文预算，应拆分输入')
                break
            packed.append(event);used+=size
        packed.reverse()
        packed=[(event,self.event_message(event)) for event in packed]
        messages.extend(msg for _,msg in packed)
        self.text_tokens=used
        current_assets=[asset for event,_ in packed if event.id in current_ids for asset in media_ids(event)]
        messages.extend(await self.attachments(current_assets))
        messages.append({'role':'user','content':'当前待处理消息：'+', '.join(ref for ref,eid in self.refs.events.items() if eid in current_ids)})
        return messages

    @staticmethod
    def _text(message):
        content=message.get('content','')
        if isinstance(content,str):return content
        return '\n'.join(part.get('text','') for part in content if part.get('type')=='text')
