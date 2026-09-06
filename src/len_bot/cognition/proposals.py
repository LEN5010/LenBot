"""Small native proposal tools stage changes; finish_turn commits one ledger."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, TaskProposal
from len_bot.memory.models import MemoryProposal


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class TextPart(StrictModel):
    type: Literal['text']
    text: str=Field(min_length=1)

class ImagePart(StrictModel):
    type: Literal['image']
    asset_id: str=Field(min_length=1,description='图片I或运营表情P引用')

class ReplyExpectation(StrictModel):
    target: str=Field(description='期待回应的人物U引用')
    intent: str=Field(min_length=1)

class TurnMessage(StrictModel):
    segments:list[Annotated[TextPart|ImagePart,Field(discriminator='type')]]=Field(min_length=1,max_length=12)
    reply_to: str|None=Field(default=None,description='可选消息M引用')
    ack_ref: str|None=Field(default=None,description='仅用于确认本轮start_work/schedule_reminder新建事项的proposal_ref；其他暂存S引用用于discard_proposal')
    delivery_ref: str|None=Field(default=None,description='本条送达后完成的工作J或提醒T；工作只接受completed/partial执行结果，失败通知不用此字段')
    work_ref: str|None=Field(default=None,description='本条进展或结果所依据的工作J')
    expect_reply: ReplyExpectation|None=None

class FinishTurn(StrictModel):
    messages:list[TurnMessage]=Field(max_length=3,description='零至三条；空列表表示沉默')
    note:str=Field(default='',max_length=500,description='内部参与判断，不发送，不保存为长期认识')

class Evidence(StrictModel):
    evidence:list[str]=Field(min_length=1,description='本轮实际读过的消息M引用')

class StartWork(Evidence):
    proposal_ref:str=Field(min_length=1,description='你为本轮提案指定的短名字，可用于消息ack_ref')
    goal:str=Field(min_length=1)
    constraints:list[str]=Field(default_factory=list)
    result_refs:list[str]=Field(default_factory=list,description='复用本群已有资料R')

class ReviseWork(Evidence):
    work_ref:str
    goal:str|None=None
    constraints_add:list[str]=Field(default_factory=list)
    constraints_remove:list[str]=Field(default_factory=list)

class ControlWork(Evidence):
    work_ref:str

class ScheduleReminder(Evidence):
    model_config=ConfigDict(extra='forbid',json_schema_extra={'oneOf':[
        {'required':['due_at'],'properties':{'due_at':{'type':'number'},'delay_seconds':{'type':'null'}}},
        {'required':['delay_seconds'],'properties':{'delay_seconds':{'type':'number'},'due_at':{'type':'null'}}},
    ]})
    proposal_ref:str=Field(min_length=1)
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
    'start_work':(StartWork,'建立后台只读工作；主动搜索陌生概念、外部事实和当前信息，也用于计算、解题和整理。群友不必另行要求搜索；与finish_turn一起提交后开始执行。'),
    'revise_work':(ReviseWork,'按新消息修订实际工作目标或约束，保留已有资料与预算。'),
    'cancel_work':(ControlWork,'取消工作；本轮终结并提交后生效。'),
    'resume_work':(ControlWork,'恢复当前can_resume=true的失败或中断工作；保持已有预算与资料，部分结果不因此重开。'),
    'schedule_reminder':(ScheduleReminder,'按明确请求建立定时提醒。'),
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

FINISH_TURN=definition('finish_turn',FinishTurn,'提交剩余暂存提案及零至三条消息；不再需要的提案先用discard_proposal撤回。空消息不丢弃提案，只有事务获准的消息才发送。')


class ProposalLedger:
    def __init__(self,context,episode_id):
        self.context=context
        self.episode_id=episode_id
        self.jobs=[];self.tasks=[];self.memories=[];self.loops=[]
        self.proposal_refs=set()
        self.staged={}
        self._next_handle=1

    def definitions(self):return [definition(name,*value) for name,value in TOOLS.items()]

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
            proposal_ref=getattr(model,'proposal_ref',None)
            if proposal_ref and proposal_ref in self.staged:raise ValueError('本轮proposal_ref重复')
            if name=='start_work':
                collection='jobs'
                value=JobProposal(proposal_id=proposal_ref,goal=model.goal,constraints_add=model.constraints,
                    source_event_ids=evidence,result_ids=[refs.result_id(r) for r in model.result_refs])
            elif name in {'revise_work','cancel_work','resume_work'}:
                job=refs.job(model.work_ref)
                collection='jobs'
                value=JobProposal(operation={'revise_work':'revise','cancel_work':'cancel','resume_work':'resume'}[name],
                    job_id=job['id'],expected_revision=job['revision'],source_event_ids=evidence,
                    goal=getattr(model,'goal',None),constraints_add=getattr(model,'constraints_add',[]),constraints_remove=getattr(model,'constraints_remove',[]))
            elif name=='schedule_reminder':
                collection='tasks'
                value=TaskProposal(proposal_id=proposal_ref,description=model.description,due_at=model.due_at,
                    delay_seconds=model.delay_seconds,
                    requester_id=refs.actor_id(model.requester),target_actor_id=refs.actor_id(model.target or model.requester),
                    source_event_ids=evidence,payload={'kind':'reminder'},origin_episode_id=self.episode_id)
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
            if proposal_ref:self.proposal_refs.add(proposal_ref)
            else:
                while f'S{self._next_handle}' in self.staged:self._next_handle+=1
                proposal_ref=f'S{self._next_handle}';self._next_handle+=1
            getattr(self,collection).append(value)
            self.staged[proposal_ref]=(collection,value)
            return {'status':'staged','proposal_ref':proposal_ref,
                    'note':'尚未提交；可用discard_proposal撤回本条，finish_turn统一提交剩余提案。此引用不是实际工作J或提醒T'}
        except (ValueError,KeyError) as error:raise ToolArgumentError(str(error)) from error

    async def finish(self,arguments):
        try:
            result=FinishTurn.model_validate(arguments)
            refs=self.context.refs;messages=[]
            for item in result.messages:
                if item.ack_ref and item.ack_ref not in self.proposal_refs:
                    raise ValueError('ack_ref没有对应本轮提案。当前已暂存的新建事项引用：'
                        + ', '.join(sorted(self.proposal_refs)) + '。引用字段本身不会创建工作；'
                        '需要查询时先调用start_work取得staged回执，再调用finish_turn确认。')
                parts=[{'type':'text','text':p.text} if isinstance(p,TextPart) else {'type':'image','asset_id':refs.media_id(p.asset_id)} for p in item.segments]
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
        except (ValueError,KeyError) as error:raise TerminalArgumentError(str(error)) from error
