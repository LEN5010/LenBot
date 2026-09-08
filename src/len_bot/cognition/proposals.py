"""Small native proposal tools stage changes; finish_turn commits one ledger."""
from __future__ import annotations

import copy
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
from len_bot.cognition.jobs import GroupSummaryRange, JobProposal
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.memory.models import MemoryProposal


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class TurnPart(StrictModel):
    text: str|None=Field(default=None,min_length=1)
    image: str|None=Field(default=None,min_length=1)

    @model_validator(mode='after')
    def one_content(self):
        if self.model_fields_set not in ({'text'}, {'image'}) or not (self.text or self.image):
            raise ValueError('每个片段必须且只能填写一个非空text或image字段')
        return self

class ReplyExpectation(StrictModel):
    target: str=Field(description='期待回应的人物U引用')
    intent: str=Field(min_length=1)

class TurnMessage(StrictModel):
    segments:list[TurnPart]=Field(min_length=1,max_length=12)
    reply_to: str|None=Field(default=None,description='可选消息M引用')
    ack_ref: str|None=Field(default=None,description='复制本轮start_work/schedule_reminder回执中的ack_ref')
    delivery_ref: str|None=Field(default=None,description='本条送达后完成的工作J或提醒T；工作只接受completed/partial执行结果，失败通知不用此字段')
    work_ref: str|None=Field(default=None,description='本条进展或结果所依据的工作J')
    expect_reply: ReplyExpectation|None=None

class FinishTurn(StrictModel):
    messages:list[TurnMessage]=Field(max_length=3,description='零至三条；空列表表示沉默')
    note:str=Field(default='',max_length=500,description='内部参与判断，不发送，不保存为长期认识')

class Evidence(StrictModel):
    evidence:list[str]=Field(min_length=1,description='本轮实际读过的消息M引用')

class StartWork(Evidence):
    goal:str=Field(min_length=1,description='保留原问题指定的公司、型号、对象与所求结果；未核实的同名猜测不替代目标')
    constraints:list[str]=Field(default_factory=list)
    result_refs:list[str]=Field(default_factory=list,description='复用本群已有资料R')

class ReviseWork(Evidence):
    work_ref:str
    goal:str|None=None
    constraints_add:list[str]=Field(default_factory=list)
    constraints_remove:list[str]=Field(default_factory=list)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    focus: str | None = None

    @model_validator(mode='after')
    def range_pair(self):
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError('修订总结时间时必须同时提供start_at和end_at')
        if self.start_at is not None and self.start_at >= self.end_at:
            raise ValueError('总结范围必须满足start_at < end_at')
        return self

class ControlWork(Evidence):
    work_ref:str

class ScheduleReminder(Evidence):
    model_config=ConfigDict(extra='forbid',json_schema_extra={'oneOf':[
        {'required':['due_at'],'properties':{'due_at':{'type':'number'},'delay_seconds':{'type':'null'}}},
        {'required':['delay_seconds'],'properties':{'delay_seconds':{'type':'number'},'due_at':{'type':'null'}}},
    ]})
    description:str=Field(min_length=1)
    due_at:float|None=Field(default=None,allow_inf_nan=False,description='绝对Unix时间，需晚于当前时间；与delay_seconds二选一')
    delay_seconds:float|None=Field(default=None,gt=0,allow_inf_nan=False,description='从事务提交时起等待的秒数；如20分钟后填1200，与due_at二选一')
    requester:str=Field(description='请求人的U引用')
    target:str|None=Field(default=None,description='提醒对象的U引用，省略为请求人')

    @model_validator(mode='after')
    def exactly_one_time(self):
        if (self.due_at is None)==(self.delay_seconds is None):
            raise ValueError('schedule_reminder必须且只能提供due_at或delay_seconds之一')
        return self

class UpdateReminder(Evidence):
    reminder_ref:str
    due_at:float
    description:str=''

class CancelReminder(Evidence):
    reminder_ref:str

class Remember(Evidence):
    subject:str=Field(description='人物U或本群GROUP引用')
    kind:Literal['address','preference','group_norm']
    statement:str=Field(min_length=1,description='明确表达的称呼、偏好或相处要求')
    expires_at:float|None=None

class RefuteMemory(Evidence):
    memory_ref:str
    reason:str=Field(min_length=1)

class SupersedeMemory(Remember):
    memory_refs:list[str]=Field(min_length=1)
    reason:str=Field(min_length=1)

class ResolveWait(StrictModel):
    wait_ref:str


class DiscardProposal(StrictModel):
    proposal_ref:str=Field(min_length=1,description='本轮工具返回的暂存提案引用；不是已提交的工作J或提醒T')


TOOLS={
    'start_work':(StartWork,'建立后台只读工作：查询陌生概念、外部或当前事实，也用于计算、解题和整理。先调用本工具，再把回执中的ack_ref复制到finish_turn的确认消息；引用由工具生成，无需自拟。'),
    'revise_work':(ReviseWork,'按新消息修订实际工作目标或约束，保留已有资料与预算。'),
    'cancel_work':(ControlWork,'取消工作；本轮终结并提交后生效。'),
    'resume_work':(ControlWork,'恢复当前can_resume=true的失败或中断工作；保持已有预算与资料，部分结果不因此重开。'),
    'schedule_reminder':(ScheduleReminder,'按明确请求建立定时提醒；收到暂存回执后，把ack_ref复制到finish_turn的确认消息。'),
    'update_reminder':(UpdateReminder,'根据新约定更新提醒时间。'),
    'cancel_reminder':(CancelReminder,'取消已有提醒。'),
    'remember':(Remember,'保存有原话证据的明确称呼、偏好或相处要求。'),
    'refute_memory':(RefuteMemory,'依照新证据撤销已有认识，保留历史。'),
    'supersede_memory':(SupersedeMemory,'用新的明确表达修订同一主体同类认识，保留旧记录。'),
    'resolve_wait':(ResolveWait,'结束已实际送达后建立的等待回应。'),
    'discard_proposal':(DiscardProposal,'撤回本轮尚未提交的单条提案。用户改变要求时先撤回旧提案；实际工作J/提醒T应使用cancel_work/cancel_reminder。'),
}


def definition(name,model,description):
    return {'type':'function','function':{'name':name,'description':description,'parameters':model.model_json_schema()}}

def _object(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


# The model sees one small object shape, not Pydantic's discriminator/ref graph.
# Local validation remains authoritative, including exactly one content field.
FINISH_TURN={
    'type':'function',
    'function':{
        'name':'finish_turn',
        'description':'提交剩余暂存提案及零至三条消息；空messages表示沉默，但仍提交提案。片段只填text或image，不填type。新建事项确认须将暂存回执S填入ack_ref；已存在工作J的进展或结果用work_ref，可履约工作J或提醒T的最终交付用delivery_ref。只使用当前提供的引用字段和枚举值。',
        'parameters':_object({
            'messages':{'type':'array','maxItems':3,'items':_object({
                'segments':{'type':'array','minItems':1,'maxItems':12,'items':{
                    **_object({'text':{'type':'string','minLength':1},
                               'image':{'type':'string','minLength':1,'description':'本轮图片I或运营表情P引用'}}),
                    'description':'恰好一个字段：{"text":"一句回应"}或{"image":"本轮图片引用"}；可单图或按顺序混排。'}},
                'reply_to':{'type':'string','description':'可选的已读消息M引用'},
                'expect_reply':_object({'target':{'type':'string','description':'等待回应的人物U引用'},
                                        'intent':{'type':'string','minLength':1}},('target','intent')),
            },('segments',))},
            'note':{'type':'string','maxLength':500,'description':'可省略的内部参与判断，不发送'},
        },('messages',)),
    },
}


class ProposalLedger:
    def __init__(self, context, episode_id, requester_qq_uid):
        self.context=context
        self.episode_id=episode_id
        self.requester_qq_uid = requester_qq_uid
        self.jobs=[];self.tasks=[];self.memories=[];self.loops=[]
        self.proposal_refs=set()
        self.staged={}
        self._next_handle=1

    def stage_group_summary(self, *, start_at, end_at, focus, evidence):
        refs = self.context.refs
        if not refs.scene_id.startswith('group:') or not self.requester_qq_uid:
            raise ToolArgumentError('群聊总结需要当前群中的真实请求者')
        sources = [refs.event_id(reference) for reference in evidence]
        request = GroupSummaryRange(start_at=start_at, end_at=end_at, focus=focus,
            snapshot_rowid=refs.cutoff, snapshot_at=self.context.runtime.clock(),
            bot_actor_id=self.context.runtime.bot_actor_id)
        proposal_ref = f'S{self._next_handle}'
        proposal = JobProposal(proposal_id=proposal_ref,
            goal=f'总结本群 [{request.start_at.isoformat()}, {request.end_at.isoformat()}) 的已保存群聊。关注：{focus}',
            source_event_ids=sources, requester_qq_uid=self.requester_qq_uid,
            work_operation='group_summary', summary_range=request)
        self._next_handle += 1
        self.jobs.append(proposal)
        self.proposal_refs.add(proposal_ref)
        self.staged[proposal_ref] = ('jobs', proposal)
        return {'status':'staged', 'proposal_ref':proposal_ref, 'ack_ref':proposal_ref,
                'summary_range':request.model_dump(mode='json'),
                'note':'本群总结尚未创建工作；finish_turn提交后才进入原工作运行器，确认消息必须使用本轮ack_ref。'}

    def terminal_definition(self):
        result=copy.deepcopy(FINISH_TURN)
        refs=self.context.refs
        message=result['function']['parameters']['properties']['messages']['items']
        props=message['properties']
        if self.proposal_refs:
            props['ack_ref']={'type':'string','enum':sorted(self.proposal_refs),
                'description':'复制新建工作或提醒的暂存回执S；每条消息必须绑定，每个回执只确认一次。需要多段确认时写在同一消息的segments中；消息与提案一起提交，不提前写工作结论'}
            message['required'].append('ack_ref')
        job_refs=sorted(ref for ref,job in refs.jobs.items()
            if job['status'] in {'pending','claimed','processing','result_ready','awaiting_delivery','review_required','failed'})
        if job_refs:
            props['work_ref']={'type':'string','enum':job_refs,
                'description':'本条进展或结果所依据的已存在工作J；暂存回执S只能填ack_ref'}
        delivery_refs=sorted(list(refs.tasks)
            + [ref for ref,job in refs.jobs.items() if job['status']=='result_ready'
               and job['execution_status'] in {'completed','partial'}])
        if delivery_refs:
            props['delivery_ref']={'type':'string','enum':delivery_refs,
                'description':'本条真实送达后完成的实际工作J或提醒T；暂存回执S只能填ack_ref'}
        return result

    def definitions(self):
        refs=self.context.refs
        names={'start_work','schedule_reminder','remember'}
        if any(job.get('status') not in {'completed','cancelled','shadow_observed'} for job in refs.jobs.values()):
            names.update({'revise_work','cancel_work'})
        if any(job.get('can_resume') for job in refs.jobs.values()):names.add('resume_work')
        if refs.editable_tasks:names.update({'update_reminder','cancel_reminder'})
        if refs.editable_memories:names.update({'refute_memory','supersede_memory'})
        if refs.active_loops:names.add('resolve_wait')
        if self.staged:names.add('discard_proposal')
        return [definition(name,*value) for name,value in TOOLS.items() if name in names]

    async def stage(self,name,arguments):
        try:
            model=TOOLS[name][0].model_validate(arguments)
            refs=self.context.refs
            if name=='discard_proposal':
                entry=self.staged.pop(model.proposal_ref,None)
                if entry is None:raise ValueError('未找到本轮暂存提案；已提交的工作J或提醒T不能用discard_proposal撤回')
                collection,value=entry
                values=getattr(self,collection)
                if collection=='loops':values.remove(value)
                else:values[:]=[item for item in values if item is not value]
                self.proposal_refs.discard(model.proposal_ref)
                return {'status':'discarded','proposal_ref':model.proposal_ref,
                        'note':'仅撤回本轮尚未提交的提案，未修改任何实际工作或提醒；其余暂存提案仍待finish_turn统一提交'}
            evidence=[refs.event_id(ref) for ref in getattr(model,'evidence',[])]
            proposal_ref=f'S{self._next_handle}'
            if name=='start_work':
                collection='jobs'
                value=JobProposal(proposal_id=proposal_ref,goal=model.goal,constraints_add=model.constraints,
                    source_event_ids=evidence,result_ids=[refs.result_id(r) for r in model.result_refs],
                    requester_qq_uid=self.requester_qq_uid)
            elif name in {'revise_work','cancel_work','resume_work'}:
                job=refs.job(model.work_ref)
                collection='jobs'
                summary_range = None
                if name == 'revise_work' and (model.start_at is not None or model.focus is not None):
                    if job['work_operation'] != 'group_summary':
                        raise ValueError('只有总结工作可以修订总结范围或focus')
                    values = dict(job['summary_range'])
                    if model.start_at is not None:
                        values.update(start_at=model.start_at, end_at=model.end_at,
                                      snapshot_rowid=refs.cutoff, snapshot_at=self.context.runtime.clock())
                    if model.focus is not None:
                        values['focus'] = model.focus
                    summary_range = GroupSummaryRange.model_validate(values)
                value=JobProposal(operation={'revise_work':'revise','cancel_work':'cancel','resume_work':'resume'}[name],
                    job_id=job['id'],expected_revision=job['revision'],source_event_ids=evidence,
                    goal=getattr(model,'goal',None),constraints_add=getattr(model,'constraints_add',[]),constraints_remove=getattr(model,'constraints_remove',[]),
                    requester_qq_uid=job['requester_qq_uid'], work_operation=job['work_operation'], summary_range=summary_range)
            elif name=='schedule_reminder':
                collection='tasks'
                value=TaskProposal(proposal_id=proposal_ref,description=model.description,due_at=model.due_at,
                    delay_seconds=model.delay_seconds,
                    requester_id=refs.actor_id(model.requester),target_actor_id=refs.actor_id(model.target or model.requester),
                    source_event_ids=evidence,payload={'kind':'reminder','requester_qq_uid':self.requester_qq_uid},origin_episode_id=self.episode_id)
            elif name in {'update_reminder','cancel_reminder'}:
                collection='tasks'
                value=TaskProposal(operation='update' if name=='update_reminder' else 'cancel',
                    task_id=refs.task_id(model.reminder_ref),due_at=getattr(model,'due_at',None),description=getattr(model,'description',''),source_event_ids=evidence)
            elif name in {'remember','supersede_memory'}:
                collection='memories'
                value=MemoryProposal(operation='create' if name=='remember' else 'supersede',
                    subject=refs.actor_id(model.subject),kind=model.kind,statement=model.statement,basis='reported',
                    evidence=evidence,expires_at=model.expires_at,scope=refs.scene_id,reason=getattr(model,'reason',''),
                    target_memory_ids=[refs.memory_id(r) for r in getattr(model,'memory_refs',[])])
            elif name=='refute_memory':
                collection='memories'
                value=MemoryProposal(operation='refute',target_memory_ids=[refs.memory_id(model.memory_ref)],
                    reason=model.reason,evidence=evidence,scope=refs.scene_id)
            elif name=='resolve_wait':
                collection='loops';value=refs.loop_id(model.wait_ref)
                existing=next((ref for ref,entry in self.staged.items() if entry==(collection,value)),None)
                if existing:return {'status':'staged','proposal_ref':existing,'note':'该关闭提案已在本轮暂存，尚未提交'}
            self._next_handle+=1
            creation=name in {'start_work','schedule_reminder'}
            if creation:self.proposal_refs.add(proposal_ref)
            getattr(self,collection).append(value)
            self.staged[proposal_ref]=(collection,value)
            return {'status':'staged','proposal_ref':proposal_ref,
                    **({'ack_ref':proposal_ref} if creation else {}),
                    'note':'尚未提交；可用discard_proposal撤回本条，finish_turn统一提交剩余提案。此引用不是实际工作J或提醒T'}
        except (ValueError,KeyError) as error:
            raise ToolArgumentError(str(error)) from error

    async def finish(self,arguments):
        try:
            result=FinishTurn.model_validate(arguments)
            refs=self.context.refs;messages=[]
            if self.proposal_refs and any(not item.ack_ref for item in result.messages):
                raise ValueError('本轮仍有新建事项；每条确认消息必须填写暂存回执中的ack_ref。当前可用：'
                    + ', '.join(sorted(self.proposal_refs)) + '；这些S引用不能填入work_ref或delivery_ref。')
            for item in result.messages:
                if item.ack_ref and item.ack_ref not in self.proposal_refs:
                    raise ValueError('ack_ref没有对应本轮提案。当前已暂存的新建事项引用：'
                        + ', '.join(sorted(self.proposal_refs)) + '。引用字段本身不会创建工作；'
                        '需要查询时先调用start_work取得staged回执，再调用finish_turn确认。')
                parts=[]
                for part in item.segments:
                    if part.text is not None:
                        parts.append({'type':'text','text':part.text})
                    else:
                        asset_id=refs.media_id(part.image)
                        if asset_id not in self.context.loaded_media:
                            raise ValueError('发送图片前必须先用read_media读取本轮选定的图片像素')
                        parts.append({'type':'image','asset_id':asset_id})
                reply=None
                if item.reply_to:
                    event_id=refs.event_id(item.reply_to)
                    rows=await self.context.runtime.event_store.read_context(event_id,before=0,after=0,
                        allowed_scopes=[refs.scene_id],through_rowid=refs.cutoff)
                    reply=rows[0]['payload'].get('message_id') if rows else None
                    if reply is None:raise ValueError('引用消息没有可回复的协议message_id')
                    reply=str(reply)
                job=refs.job(item.work_ref) if item.work_ref else None
                delivery=None
                if item.delivery_ref:
                    if item.delivery_ref in refs.tasks or item.delivery_ref in refs.tasks.values():delivery=refs.task_id(item.delivery_ref)
                    else:
                        target=refs.job(item.delivery_ref)
                        if job and job['id']!=target['id']:raise ValueError('履约与工作引用不一致')
                        job=target;delivery=job['id']
                expectation=item.expect_reply
                messages.append(MessageProposal(segments=parts,reply_to=reply,task_ref=item.ack_ref,fulfils_task_id=delivery,
                    job_id=job['id'] if job else None,job_revision=job['revision'] if job else None,
                    expect_reply=bool(expectation),reply_target=refs.actor_id(expectation.target) if expectation else None,
                    reply_intent=expectation.intent if expectation else None))
            return EpisodeOutcome(disposition=FinalDisposition.ACTION if messages else FinalDisposition.SILENCE,
                decision_reason=result.note or ('参与' if messages else '旁听'),message_proposals=messages,
                task_proposals=self.tasks,job_proposals=self.jobs,memory_proposals=self.memories,resolve_open_loop_ids=self.loops)
        except (ValueError,KeyError) as error:
            raise TerminalArgumentError(str(error)) from error
