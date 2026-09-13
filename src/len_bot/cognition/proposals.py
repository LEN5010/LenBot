"""Small native proposal tools stage changes; respond commits one ledger."""
from __future__ import annotations

import copy
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
from len_bot.cognition.jobs import JobProposal
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, SourceOutcome, TaskProposal
from len_bot.memory.models import MemoryProposal
from len_bot.events.models import PluginOrigin, human_event_uid, human_initiator_for


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class TurnPart(StrictModel):
    text: str|None=Field(default=None,min_length=1,
        description='要实际发送的文字；指定照发时只填要求的文字、标点和换行，不添加角色评语。换行使用真实换行，仅在用户要求展示转义写法时发送反斜线加n。')
    image: str|None=Field(default=None,min_length=1)
    video: str|None=Field(default=None,min_length=1,description='本轮已保存的视频媒体I引用')
    audio: str|None=Field(default=None,min_length=1,description='本轮已保存的音频媒体I引用')
    at: str|None=Field(default=None,min_length=1)

    @model_validator(mode='after')
    def one_content(self):
        if self.model_fields_set not in ({'text'}, {'image'}, {'video'}, {'audio'}, {'at'}) or not (self.text or self.image or self.video or self.audio or self.at):
            raise ValueError('每个片段必须且只能填写一个非空text、image、video、audio或at字段')
        return self

class ReplyExpectation(StrictModel):
    target: str=Field(description='期待回应的人物U引用')
    intent: str=Field(min_length=1)

class TurnMessage(StrictModel):
    segments:list[TurnPart]=Field(min_length=1,max_length=12)
    reply_to: str|None=Field(default=None,description='可选消息M引用')
    source: str|None=Field(default=None,description='本条回应对应的已读来源M；普通聊天和操作确认使用人类原话，插件系统来源保留原类型，与显示引用reply_to分别表达')
    ack_ref: str|None=Field(default=None,description='复制本轮start_work/schedule_reminder回执中的ack_ref')
    operation_ref: str|None=Field(default=None,description='复制本轮控制工作、提醒或记忆操作返回的proposal_ref；只确认这项操作实际提交后的结果')
    delivery_ref: str|None=Field(default=None,description='本条送达后完成的工作J或提醒T；工作只接受completed/partial执行结果，失败通知不用此字段')
    work_ref: str|None=Field(default=None,description='本条进展或结果所依据的工作J')
    expect_reply: ReplyExpectation|None=None
    addressed_to:list[str]=Field(default_factory=list,description='实际对谁说话的成员U引用；与source、reply_to和期待回答者分别填写')

    @model_validator(mode='after')
    def one_message_relation(self):
        if sum(value is not None for value in (self.ack_ref,self.operation_ref,self.delivery_ref,self.work_ref)) > 1:
            raise ValueError('ack_ref、operation_ref、delivery_ref、work_ref每条消息只能选择一种；创建、操作确认、结果交付和普通工作引用分别表达')
        return self

class SourceResolution(StrictModel):
    source:str=Field(description='本次处理的已读来源M；继续同一请求时可沿用此前checkpoint的来源，系统来源不伪装成人类原话')
    status:Literal['replied','delegated','waiting','incomplete','silent']
    reason:str=Field(default='',max_length=500)
    unfinished:list[str]=Field(default_factory=list,description='同一原话中仍未完成的要求；未做的部分不能被已发送内容覆盖')


class Respond(StrictModel):
    messages:list[TurnMessage]=Field(max_length=3,description='零至三条；空列表表示沉默')
    sources:list[SourceResolution]
    next:Literal['end','continue','wait']
    note:str=Field(default='',max_length=500,description='内部参与判断；尚有待处理来源但本次不处理任何来源时，说明等待条件或结束原因。不发送、不保存为长期认识')
    release_focus:list[str]=Field(default_factory=list,description='根据本人原话停止本次误接或互动的成员U；只撤销现有关注窗口，不写长期规则')

class Evidence(StrictModel):
    evidence:list[str]=Field(min_length=1,description='本轮实际读过的消息M引用')

class StartWork(Evidence):
    request_source:str=Field(description='提出这项委托的已读人类消息M；据此确定请求者，其他资料填evidence')
    goal:str=Field(min_length=1,description='保留原问题指定的公司、型号、对象与所求结果；未核实的同名猜测不替代目标')
    constraints:list[str]=Field(default_factory=list)
    result_refs:list[str]=Field(default_factory=list,description='复用本群已有资料R')

class ReviseWork(Evidence):
    work_ref:str
    goal:str|None=None
    constraints_add:list[str]=Field(default_factory=list)
    constraints_remove:list[str]=Field(default_factory=list)
    parameters: dict | None = Field(default=None,description='仅专用插件工作使用；按该工作提供的work_revision_schema填写业务参数变化')

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
    request_source:str=Field(description='提出提醒委托的已读人类消息M；请求者由这条原话确定')
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
    'start_work':(StartWork,'建立需要长时间、多页资料或持续进度的后台只读工作；短读取和计算可直接使用本轮工具。先调用本工具，再把回执中的ack_ref复制到respond的确认消息；引用由工具生成，无需自拟。'),
    'revise_work':(ReviseWork,'按新消息修订实际工作目标或约束，保留已有资料与预算。取得回执后用operation_ref确认本次操作，不用work_ref确认新版本。'),
    'cancel_work':(ControlWork,'取消工作；本轮终结并提交后生效。确认消息用本回执的operation_ref，不同时交付旧结果。'),
    'resume_work':(ControlWork,'显式继续can_resume=true的失败/中断工作，或有未完成范围且交付状态已确定的partial工作；保留原工作ID、已用预算和资料，新版本不重复旧交付。取得回执后用operation_ref确认。'),
    'schedule_reminder':(ScheduleReminder,'按明确请求建立定时提醒；收到暂存回执后，把ack_ref复制到respond的确认消息。'),
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
RESPOND={
    'type':'function',
    'function':{
        'name':'respond',
        'description':'提交剩余暂存提案及零至三条消息；空messages表示沉默，但仍提交提案。新建确认用ack_ref；控制或记忆操作确认用operation_ref；普通工作说明用work_ref；最终履约用delivery_ref。每条消息只选一种关系，操作确认仅在对应事务成功后成立。片段只填text、image、video、audio或at，不填type。',
        'parameters':_object({
            'messages':{'type':'array','maxItems':3,'items':_object({
                'segments':{'type':'array','minItems':1,'maxItems':12,'items':{
                    **_object({'text':{'type':'string','minLength':1},
                               'image':{'type':'string','minLength':1,'description':'本轮图片I或运营表情P引用'},
                               'video':{'type':'string','minLength':1,'description':'本轮已保存视频媒体I引用'},
                               'audio':{'type':'string','minLength':1,'description':'本轮已保存音频媒体I引用'},
                               'at':{'type':'string','minLength':1,'description':'真实成员提及，填写本轮人物U引用'}}),
                    'description':'恰好一个字段：text、image、video、audio或at；按顺序混排。'}},
                'reply_to':{'type':'string','description':'可选的已读消息M引用'},
                'source':{'type':'string','description':'本条回应对应的已读来源M；普通聊天和操作确认须来自人类原话，插件系统来源保留原类型'},
                'addressed_to':{'type':'array','items':{'type':'string'},'uniqueItems':True,
                                'description':'本条实际回应的成员U；请求者和引用作者不自动成为回应对象'},
                'expect_reply':_object({'target':{'type':'string','description':'等待回应的人物U引用'},
                                        'intent':{'type':'string','minLength':1}},('target','intent')),
            },('segments',))},
            'note':{'type':'string','maxLength':500,'description':'内部参与判断；不处理任何待处理来源时必须说明等待条件或结束原因，不发送'},
            'sources':{'type':'array','items':_object({
                'source':{'type':'string','description':'本次处理的原话M'},
                'status':{'type':'string','enum':['replied','delegated','waiting','incomplete','silent']},
                'reason':{'type':'string','maxLength':500},
                'unfinished':{'type':'array','items':{'type':'string'}}},('source','status')),
                'description':'逐来源保留本次处理去向及未完成要求；未处理的独立原话不要列入'},
            'next':{'type':'string','enum':['end','continue','wait'],
                'description':'end结束本轮；continue提交后在原预算继续；wait提交一个真实等待关系，释放模型资源后等对应回应'},
            'release_focus':{'type':'array','items':{'type':'string'},'uniqueItems':True,
                'description':'根据本人已读原话停止本次互动的成员U；撤销现有短时关注'},
        },('messages','sources','next')),
    },
}


class ProposalLedger:
    def __init__(self, context, episode_id):
        self.context=context
        self.episode_id=episode_id
        self.jobs=[];self.tasks=[];self.memories=[];self.loops=[]
        self.proposal_refs=set()
        self.staged={}
        self._next_handle=1
        self.checkpoint_index=0
        self.messages_committed=0
        self.continuing_sources=set()
        self.plugin_source_ids=set()
        self.remaining_model_calls=lambda:0

    async def request_source(self, reference):
        event_id = self.context.refs.event_id(reference)
        return await self.request_event(event_id)

    async def request_event(self, event_id):
        refs = self.context.refs
        if event_id not in refs.read_events:
            raise ValueError('请求来源原话尚未完整读取；先读对应消息，再绑定来源')
        events = await self.context.runtime.event_store.events_by_ids(refs.scene_id, [event_id], refs.cutoff)
        if len(events) != 1 or human_event_uid(events[0]) is None or events[0].actor_id == refs.bot_actor_id:
            raise ValueError('请求来源必须是当前场景与截点内已读的人类原话')
        return events[0]

    @staticmethod
    def human_source(event):
        """The typed human branch for one real request source, or refuse."""
        initiator = human_initiator_for(event)
        if initiator is None:
            raise ValueError('人类发起者只能来自当前场景中真实人物的原话')
        return initiator

    async def stage_plugin_work(self, call, *, goal, evidence, request_source, parameters=None, constraints=(), result_refs=()):
        refs = self.context.refs
        source = await self.request_source(request_source)
        sources = list(dict.fromkeys([source.id, *(refs.event_id(reference) for reference in evidence)]))
        work=call.plugin.spec.work if parameters is not None else None
        if parameters is not None and (work is None or not isinstance(parameters,work.parameters_model)):
            raise ToolArgumentError('Plugin work parameters require the owning descriptor model')
        encoded=parameters.model_dump(mode='json') if parameters is not None else None
        for proposal in self.jobs:
            if (proposal.operation=='create' and proposal.plugin_origin
                    and proposal.plugin_origin.plugin_id==call.origin.plugin_id
                    and proposal.plugin_origin.plugin_version==call.origin.plugin_version
                    and proposal.request_source_event_id==source.id and proposal.goal==goal
                    and proposal.work_parameters==encoded):
                return {'status':'staged','proposal_ref':proposal.proposal_id,'ack_ref':proposal.proposal_id,
                    'work_parameters':encoded,'note':'同一请求的这项工作已暂存；使用原引用，不重复创建或确认。'}
        proposal_ref = f'S{self._next_handle}'
        proposal = JobProposal(proposal_id=proposal_ref,
            goal=goal,constraints_add=list(constraints),result_ids=[refs.result_id(reference) for reference in result_refs],
            source_event_ids=sources, requester_qq_uid=source.actor_id.removeprefix('user:'),
            request_source_event_id=source.id, initiator=self.human_source(source),
            work_operation=work.operation if work else 'information',plugin_origin=call.origin,
            work_parameters=encoded)
        self._next_handle += 1
        self.jobs.append(proposal)
        self.proposal_refs.add(proposal_ref)
        self.staged[proposal_ref] = ('jobs', proposal)
        return {'status':'staged', 'proposal_ref':proposal_ref, 'ack_ref':proposal_ref,
                'work_parameters':proposal.work_parameters,
                'note':'工作尚未提交；respond提交后才进入原工作运行器，确认消息使用本轮ack_ref。'}

    def terminal_definition(self):
        result=copy.deepcopy(RESPOND)
        refs=self.context.refs
        message=result['function']['parameters']['properties']['messages']['items']
        props=message['properties']
        available = [ref for ref, event_id in refs.events.items()
                     if event_id in refs.read_events and (event_id in self.continuing_sources | self.plugin_source_ids
                         or any(w.event_id == event_id for w in self.context.session.pending_wakes))]
        parameters=result['function']['parameters']['properties']
        parameters['messages']['maxItems']=3-self.messages_committed
        if self.remaining_model_calls()<=1:parameters['next']['enum']=['end']
        handled = parameters['sources']
        if available:
            handled['items']['properties']['source']['enum'] = available
        else:
            handled['maxItems'] = 0
        if self.proposal_refs:
            props['ack_ref']={'type':'string','enum':sorted(self.proposal_refs),
                'description':'仅对应新建事项的确认消息填写；复制实际暂存回执S，每个回执只确认一次。其他人的普通回复不填；不提前写工作结论'}
        operation_refs = sorted(ref for ref,(collection,value) in self.staged.items()
            if collection=='memories' or collection in {'jobs','tasks'} and value.operation!='create')
        if operation_refs:
            props['operation_ref']={'type':'string','enum':operation_refs,
                'description':'复制本轮控制或记忆操作已返回的proposal_ref；只确认这一项实际操作，不能与ack_ref/work_ref/delivery_ref组合'}
        controlled_jobs={proposal.job_id for proposal in self.jobs if proposal.operation!='create'}
        job_refs=sorted(ref for ref,job in refs.jobs.items() if job['id'] not in controlled_jobs
            if not (job['status']=='result_ready' and job['execution_status'] in {'completed','partial'}
                     and job['delivery_action_id'] is None))
        if job_refs:
            props['work_ref']={'type':'string','enum':job_refs,
                'description':'本条状态说明或按需引用的实际工作J；旧事项不因此重开。首次待交付结果用delivery_ref，暂存回执S只填ack_ref'}
        delivery_refs=sorted(list(refs.tasks)
            + [ref for ref,job in refs.jobs.items() if job['id'] not in controlled_jobs and job['status']=='result_ready'
               and job['execution_status'] in {'completed','partial'} and job['delivery_action_id'] is None])
        if delivery_refs:
            props['delivery_ref']={'type':'string','enum':delivery_refs,
                'description':'本条真实送达后完成的实际工作J或提醒T；暂存回执S只能填ack_ref'}
        relations=[name for name in ('ack_ref','operation_ref','work_ref','delivery_ref') if name in props]
        if len(relations)>1:
            message['allOf']=[{'not':{'required':[left,right]}}
                for index,left in enumerate(relations) for right in relations[index+1:]]
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
            model=TOOLS[name][0].model_validate_json(json.dumps(arguments,ensure_ascii=False),strict=True)
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
                        'note':'仅撤回本轮尚未提交的提案，未修改任何实际工作或提醒；其余暂存提案仍待respond统一提交'}
            evidence=[refs.event_id(ref) for ref in getattr(model,'evidence',[])]
            if name in {'revise_work','cancel_work','resume_work','update_reminder','cancel_reminder'}:
                originals=await self.context.runtime.event_store.events_by_ids(refs.scene_id,evidence,refs.cutoff)
                if not any(event.event_type.value in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                           and event.actor_id.startswith('user:') and event.actor_id!=refs.bot_actor_id for event in originals):
                    raise ValueError('控制操作必须有本轮实际读过的人类请求原话；系统工作完成事件不能单独授权恢复、修订或取消')
            proposal_ref=f'S{self._next_handle}'
            if name=='start_work':
                source=await self.request_source(model.request_source)
                collection='jobs'
                value=JobProposal(proposal_id=proposal_ref,goal=model.goal,constraints_add=model.constraints,
                    source_event_ids=list(dict.fromkeys([source.id,*evidence])),result_ids=[refs.result_id(r) for r in model.result_refs],
                    requester_qq_uid=source.actor_id.removeprefix('user:'),request_source_event_id=source.id,
                    initiator=self.human_source(source))
            elif name in {'revise_work','cancel_work','resume_work'}:
                job=refs.job(model.work_ref)
                collection='jobs'
                parameters=None
                revised_goal=None
                if name=='revise_work' and model.parameters is not None:
                    work=self.context.runtime.plugin_host.work_spec(job['plugin_origin'],job['work_operation'])
                    if work is None:raise ValueError('This work has no plugin-specific revision parameters')
                    changes=work.revision_model.model_validate_json(json.dumps(model.parameters,ensure_ascii=False),strict=True)
                    current=work.parameters_model.model_validate(job['work_parameters'])
                    revised=work.revise(current,changes,refs.cutoff,self.context.runtime.clock())
                    parameters=work.parameters_model.model_validate(revised.parameters).model_dump(mode='json')
                    revised_goal=revised.goal
                value=JobProposal(proposal_id=proposal_ref,operation={'revise_work':'revise','cancel_work':'cancel','resume_work':'resume'}[name],
                    job_id=job['id'],expected_revision=job['revision'],source_event_ids=evidence,
                    goal=getattr(model,'goal',None) or revised_goal,constraints_add=getattr(model,'constraints_add',[]),constraints_remove=getattr(model,'constraints_remove',[]),
                    requester_qq_uid=job['requester_qq_uid'], work_operation=job['work_operation'],
                    plugin_origin=job['plugin_origin'],work_parameters=parameters)
            elif name=='schedule_reminder':
                source=await self.request_source(model.request_source)
                collection='tasks'
                value=TaskProposal(proposal_id=proposal_ref,description=model.description,due_at=model.due_at,
                    delay_seconds=model.delay_seconds,
                    requester_id=source.actor_id,target_actor_id=refs.actor_id(model.target) if model.target else source.actor_id,
                    request_source_event_id=source.id,source_event_ids=list(dict.fromkeys([source.id,*evidence])),
                    payload={'kind':'reminder','requester_qq_uid':source.actor_id.removeprefix('user:')},origin_episode_id=self.episode_id)
            elif name in {'update_reminder','cancel_reminder'}:
                collection='tasks'
                value=TaskProposal(proposal_id=proposal_ref,operation='update' if name=='update_reminder' else 'cancel',
                    task_id=refs.task_id(model.reminder_ref),due_at=getattr(model,'due_at',None),description=getattr(model,'description',''),source_event_ids=evidence)
            elif name in {'remember','supersede_memory'}:
                collection='memories'
                value=MemoryProposal(proposal_id=proposal_ref,operation='create' if name=='remember' else 'supersede',
                    subject=refs.actor_id(model.subject),kind=model.kind,statement=model.statement,basis='reported',
                    evidence=evidence,expires_at=model.expires_at,scope=refs.scene_id,reason=getattr(model,'reason',''),
                    target_memory_ids=[refs.memory_id(r) for r in getattr(model,'memory_refs',[])])
            elif name=='refute_memory':
                collection='memories'
                value=MemoryProposal(proposal_id=proposal_ref,operation='refute',target_memory_ids=[refs.memory_id(model.memory_ref)],
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
                    **({'operation_ref':proposal_ref} if collection in {'jobs','tasks','memories'} and not creation else {}),
                    'note':'尚未提交；可用discard_proposal撤回本条，respond统一提交剩余提案。此引用不是实际工作J或提醒T'}
        except (ValueError,KeyError) as error:
            raise ToolArgumentError(str(error)) from error

    async def finish(self,arguments):
        try:
            result=Respond.model_validate_json(json.dumps(arguments,ensure_ascii=False),strict=True)
            refs=self.context.refs;messages=[]
            if len(result.messages)+self.messages_committed>3:
                raise ValueError('所有checkpoint共用本轮三条消息上限')
            if result.next!='end' and self.remaining_model_calls()<=0:
                raise ValueError('原执行预算不足以继续或恢复等待，请结束并保留未完成项')
            handled=[refs.event_id(item.source) for item in result.sources]
            pending=self.plugin_source_ids or {wake.event_id for wake in self.context.session.pending_wakes}
            if len(handled) != len(set(handled)) or not set(handled).issubset(pending|self.continuing_sources):
                raise ValueError('sources只能填写本轮已读、当前待处理或本轮此前checkpoint已处理的来源，每个来源只能出现一次')
            if pending and not handled and not result.note.strip():
                raise ValueError('仍有待处理来源；空sources须在note说明等待依赖或本次结束原因，不能靠空提交反复取得预算')
            source_candidates = await self.context.runtime.event_store.events_by_ids(refs.scene_id, handled, refs.cutoff)
            source_records={event.id:event for event in source_candidates}
            source_candidates = [event for event in source_candidates if event.id in self.plugin_source_ids
                or event.event_type.value in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                and event.actor_id.startswith('user:') and event.actor_id != refs.bot_actor_id]
            controlled_jobs=[proposal.job_id for proposal in self.jobs if proposal.operation!='create']
            controlled_tasks=[proposal.task_id for proposal in self.tasks if proposal.operation!='create']
            changed_memories=[ident for proposal in self.memories for ident in proposal.target_memory_ids]
            if any(len(values)!=len(set(values)) for values in (controlled_jobs,controlled_tasks,changed_memories)):
                raise ValueError('同一轮对同一工作、提醒或认识目标只能保留一项操作；用discard_proposal撤回重复或冲突提案')
            confirmed_operations=set()
            for item in result.messages:
                if item.ack_ref and item.ack_ref not in self.proposal_refs:
                    raise ValueError('ack_ref没有对应本轮提案。当前已暂存的新建事项引用：'
                        + ', '.join(sorted(self.proposal_refs)) + '。引用字段本身不会创建工作；'
                        '需要查询时先调用start_work取得staged回执，再调用respond确认。')
                operation=None
                operation_sources=[]
                if item.operation_ref:
                    operation=self.staged.get(item.operation_ref)
                    if (operation is None or operation[0] not in {'jobs','tasks','memories'}
                            or operation[0]!='memories' and operation[1].operation=='create'):
                        raise ValueError('operation_ref只能引用本轮控制工作、提醒或记忆操作已经返回的proposal_ref；新建工作或提醒用ack_ref')
                    if item.operation_ref in confirmed_operations:
                        raise ValueError('同一操作回执只能对应一条确认消息')
                    confirmed_operations.add(item.operation_ref)
                    operation_sources=(operation[1].evidence if operation[0]=='memories' else operation[1].source_event_ids)
                parts=[]
                for part in item.segments:
                    if part.text is not None:
                        parts.append({'type':'text','text':part.text})
                    elif part.at is not None:
                        parts.append({'type':'at','qq_uid':refs.member_id(part.at).removeprefix('user:')})
                    elif part.image is not None:
                        asset_id=refs.media_id(part.image)
                        if await self.context.runtime.event_store.get_media(asset_id,[refs.scene_id,'global-safe']) is None:
                            raise ValueError('图片未登记、已停用或不属于当前场景')
                        parts.append({'type':'image','asset_id':asset_id})
                    elif part.video is not None or part.audio is not None:
                        media_ref = part.video or part.audio
                        asset_id = refs.media_id(media_ref)
                        asset = await self.context.runtime.event_store.get_media(asset_id, [refs.scene_id, 'global-safe'])
                        if asset is None:
                            raise ValueError('视频或音频未登记、已停用或不属于当前场景')
                        expected = 'video/' if part.video is not None else 'audio/'
                        if not (asset.get('mime_type') or '').startswith(expected):
                            raise ValueError('消息片段类型与媒体实际类型不一致')
                        parts.append({'type':'video' if part.video is not None else 'audio', 'asset_id':asset_id})
                reply=None
                if item.reply_to:
                    event_id=refs.event_id(item.reply_to)
                    rows=await self.context.runtime.event_store.read_context(event_id,before=0,after=0,
                        allowed_scopes=[refs.scene_id],through_rowid=refs.cutoff)
                    reply=rows[0]['payload'].get('message_id') if rows else None
                    if reply is None:raise ValueError('引用消息没有可回复的协议message_id')
                    reply=str(reply)
                job=(refs.job(operation[1].job_id) if operation and operation[0]=='jobs'
                     else refs.job(item.work_ref) if item.work_ref else None)
                delivery=None
                if item.delivery_ref:
                    if item.delivery_ref in refs.tasks or item.delivery_ref in refs.tasks.values():delivery=refs.task_id(item.delivery_ref)
                    else:
                        target=refs.job(item.delivery_ref)
                        if job and job['id']!=target['id']:raise ValueError('履约与工作引用不一致')
                        job=target;delivery=job['id']
                if job and job['id'] in controlled_jobs and not item.operation_ref:
                    raise ValueError('该工作在本轮有未提交控制；状态确认用对应operation_ref，不能同时按旧work_ref或delivery_ref发送旧版本内容')
                if delivery and delivery in controlled_tasks:
                    raise ValueError('本轮修改或取消的提醒不能同时按旧状态履约')
                if delivery and job and (job['result'] or {}).get('delivery'):
                    raise ValueError('此工作已有插件成品，由原工作交付入口提交；当前对话只处理新输入或控制要求，不重写成品或再次履约')
                if (job and not operation and job['status']=='result_ready' and job['execution_status'] in {'completed','partial'}
                        and job['delivery_action_id'] is None and not delivery and not (job['result'] or {}).get('delivery')):
                    raise ValueError('该工作已有首次待交付结果；使用delivery_ref绑定本条结果与真实送达关系，不要仅填work_ref')
                acknowledgement = self.staged[item.ack_ref][1] if item.ack_ref else None
                source_id = refs.event_id(item.source) if item.source else None
                if acknowledgement:
                    if source_id and source_id != acknowledgement.request_source_event_id:
                        raise ValueError('确认消息来源必须与该新建事项的请求原话一致')
                    source_id = acknowledgement.request_source_event_id
                elif operation and not source_id:
                    related=[event for event in source_candidates if event.id in operation_sources]
                    if len(related)==1:
                        source_id=related[0].id
                    else:
                        originals=await self.context.runtime.event_store.events_by_ids(refs.scene_id,operation_sources,refs.cutoff)
                        humans=[event for event in originals if event.id in refs.read_events
                                and event.event_type.value in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                                and event.actor_id.startswith('user:') and event.actor_id!=refs.bot_actor_id]
                        if len(humans)==1:source_id=humans[0].id
                elif job and not source_id:
                    source_id = job['request_source_event_id']
                elif delivery and not source_id:
                    task = next((row for row in await self.context.runtime.event_store.scene_tasks(refs.scene_id)
                                 if row['id'] == delivery), None)
                    if task is None:
                        raise ValueError('提醒不属于当前场景')
                    source_id = task['payload'].get('request_source_event_id')
                if not source_id and len(source_candidates) == 1:
                    source_id = source_candidates[0].id
                if not source_id:
                    raise ValueError('本条消息缺少明确请求归属；用source填写对应的已读人类消息M')
                if operation and source_id not in operation_sources:
                    raise ValueError('操作确认的source必须是该操作实际读取的原话证据，不能借用另一人的请求')
                if source_id in self.plugin_source_ids and not (acknowledgement or operation or job or delivery):
                    originals = await self.context.runtime.event_store.events_by_ids(refs.scene_id, [source_id], refs.cutoff)
                    if len(originals) != 1:
                        raise ValueError('插件表达缺少当前场景的真实来源')
                    source = originals[0]
                    requester = source.actor_id[5:] if source.actor_id.startswith('user:') and source.actor_id != refs.bot_actor_id else None
                else:
                    source = await self.request_event(source_id)
                    requester = source.actor_id.removeprefix('user:')
                # An explicit follow-up may come from another participant.
                # Message ownership follows that human source; the referenced
                # work keeps its own original requester and revision.
                expectation=item.expect_reply
                addressed=list(dict.fromkeys(refs.member_id(ref) for ref in item.addressed_to))
                if expectation and refs.member_id(expectation.target)==refs.bot_actor_id:
                    raise ValueError('不能把自己作为外部等待回应对象')
                message_owner=None
                if not operation:
                    if job and job['plugin_origin']:message_owner=PluginOrigin.model_validate(job['plugin_origin'])
                    elif isinstance(acknowledgement,JobProposal):message_owner=acknowledgement.plugin_origin
                    elif isinstance(acknowledgement,TaskProposal) and acknowledgement.payload.get('plugin_origin'):
                        message_owner=PluginOrigin.model_validate(acknowledgement.payload['plugin_origin'])
                    elif delivery and not job:
                        delivered_task=next((row for row in await self.context.runtime.event_store.scene_tasks(refs.scene_id)
                            if row['id']==delivery),None)
                        if delivered_task and delivered_task['payload'].get('plugin_origin'):
                            message_owner=PluginOrigin.model_validate(delivered_task['payload']['plugin_origin'])
                messages.append(MessageProposal(segments=parts,reply_to=reply,task_ref=item.ack_ref,operation_ref=item.operation_ref,fulfils_task_id=delivery,
                    source_event_id=source.id,requester_qq_uid=requester,
                    plugin_origin=message_owner,
                    addressed_to=addressed,
                    job_id=job['id'] if job else None,job_revision=job['revision'] if job else None,
                    expect_reply=bool(expectation),reply_target=refs.member_id(expectation.target) if expectation else None,
                    reply_intent=expectation.intent if expectation else None))
            affected={message.source_event_id for message in messages}
            affected.update(proposal.request_source_event_id for proposal in [*self.jobs,*self.tasks]
                            if proposal.operation=='create')
            if (affected & pending) - set(handled):
                raise ValueError('本次已回应或已委托的请求来源必须列入sources，其它仅仅读到的来源继续保留')
            outcomes=[]
            for item,ident in zip(result.sources,handled):
                original=source_records[ident]
                runtime_source=original.event_type.value not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                related_indices=[index for index,message in enumerate(messages) if message.source_event_id==ident
                    or runtime_source and (message.job_id and message.job_id==original.payload.get('job_id')
                        or message.fulfils_task_id and message.fulfils_task_id==original.payload.get('task_id')
                        or message.source_event_id and message.source_event_id==original.payload.get('origin_event_id'))]
                related_messages=[messages[index] for index in related_indices]
                related_proposals=[ref for ref,(kind,proposal) in self.staged.items()
                    if (kind=='memories' and ident in proposal.evidence
                        or kind in {'jobs','tasks'} and (proposal.request_source_event_id==ident or ident in proposal.source_event_ids))]
                if item.status=='replied' and not related_messages:
                    raise ValueError('replied必须关联本checkpoint对应来源的消息')
                if item.status=='delegated' and not any(ref in related_proposals for ref,(kind,_) in self.staged.items() if kind in {'jobs','tasks'}):
                    raise ValueError('delegated必须关联本checkpoint实际暂存的工作或提醒')
                if item.status=='waiting' and (result.next!='wait' or not any(message.expect_reply for message in related_messages)):
                    raise ValueError('waiting必须关联本checkpoint期待真实回应的消息')
                if item.status in {'incomplete','silent'} and not (item.reason.strip() or item.unfinished):
                    raise ValueError('未完成或旁听须说明原因或未完成范围')
                if item.status=='silent' and (related_messages or related_proposals):
                    raise ValueError('已有消息或实际操作的来源不能标为silent')
                outcomes.append(SourceOutcome(source_event_id=ident,status=item.status,reason=item.reason,
                    unfinished=item.unfinished,proposal_refs=related_proposals,message_indices=related_indices))
            if result.next=='wait' and (sum(message.expect_reply for message in messages)!=1
                    or not any(item.status=='waiting' for item in outcomes) or self.messages_committed+len(messages)>=3):
                raise ValueError('wait需要且只能有一个真实等待对象及waiting来源，并保留后续表达的消息额度')
            unlinked={ref for ref,(kind,_) in self.staged.items() if kind!='loops'}-{ref for source in outcomes for ref in source.proposal_refs}
            if unlinked:
                raise ValueError('实际提交的提案须有对应来源的处理结果：'+', '.join(sorted(unlinked)))
            released=list(dict.fromkeys(refs.member_id(ref) for ref in result.release_focus))
            if set(released)-{event.actor_id for event in source_candidates}:
                raise ValueError('撤销关注必须有本次处理的本人原话，不能替其他人结束互动')
            return EpisodeOutcome(disposition=FinalDisposition.ACTION if messages else FinalDisposition.SILENCE,
                decision_reason=result.note or ('参与' if messages else '旁听'),message_proposals=messages,
                source_outcomes=outcomes,checkpoint_index=self.checkpoint_index,next_action=result.next,
                release_focus_actor_ids=released,
                task_proposals=self.tasks,job_proposals=self.jobs,memory_proposals=self.memories,resolve_open_loop_ids=self.loops)
        except TerminalArgumentError:
            raise
        except (ValueError,KeyError) as error:
            raise TerminalArgumentError(str(error)) from error

    async def validate_message_segments(self, outcome):
        """A plugin hook still uses the same known members and scene assets."""
        for message in outcome.message_proposals:
            for segment in message.segments:
                if segment.type == 'at' and 'user:' + segment.qq_uid not in self.context.refs.actors.values():
                    raise TerminalArgumentError('提交前处理增加的提及对象没有出现在本轮人物资料中')
                if segment.type in {'image', 'video', 'audio'} and await self.context.runtime.event_store.get_media(
                        segment.asset_id, [self.context.refs.scene_id, 'global-safe']) is None:
                    raise TerminalArgumentError('提交前处理的媒体不在当前场景可用素材中')

    def adopt_commit(self,outcome):
        if outcome.checkpoint_index<self.checkpoint_index:return
        self.continuing_sources.update(outcome.handled_source_event_ids)
        self.messages_committed+=len(outcome.message_proposals)
        self.checkpoint_index+=1
        self.jobs=[];self.tasks=[];self.memories=[];self.loops=[]
        self.proposal_refs=set();self.staged={}
