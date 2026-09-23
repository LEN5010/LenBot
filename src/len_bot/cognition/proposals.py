"""Small native proposal tools stage changes; respond commits one ledger."""
from __future__ import annotations

import copy
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from len_bot.cognition.agent_loop import TerminalArgumentError, ToolArgumentError
from len_bot.cognition.jobs import JobProposal, ReusedWorkResult
from len_bot.cognition.models import (AnswerBasis, AnswerGap, AnswerWorkResult,
    EpisodeOutcome, FinalDisposition, MessageProposal, SourceOutcome, TaskProposal)
from len_bot.cognition.request_record import _RecordedToolDefinition
from len_bot.memory.models import MemoryProposal
from len_bot.events.models import PluginOrigin, human_event_uid, human_initiator_for
from len_bot.scheduler.models import task_delivery_available
from len_bot.runtime.attention import HUMAN_INPUTS, RUNTIME_INPUTS


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class TurnPart(StrictModel):
    model_config=ConfigDict(json_schema_extra={'oneOf':[
        {'required':[name]} for name in ('text','image','video','audio','at')]})
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

class SocialAnswerBasis(StrictModel):
    kind: Literal['social','general']


class EvidenceAnswerBasis(StrictModel):
    event_refs: list[str]=Field(default_factory=list,max_length=16,
        description='支持本条答复的已完整读取的人类原话M；与回应来源source分开，不填摘要或Bot发言')
    evidence_refs: list[str]=Field(default_factory=list,max_length=16,
        description='复制支持本条结论的实际已读资料页evidence_ref；宿主解析原资料与精确范围。群原话用event_refs，目录不作为正文依据')
    unresolved: list[AnswerGap]=Field(default_factory=list,max_length=12)

    @model_validator(mode='after')
    def unique_references(self):
        if len(set(self.event_refs)) != len(self.event_refs):
            raise ValueError('event_refs 每条已读原话只填一次')
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError('evidence_refs 每个已读资料页只填一次')
        return self


class ObservedAnswerBasis(EvidenceAnswerBasis):
    model_config=ConfigDict(json_schema_extra={'anyOf':[
        {'required':[name],'properties':{name:{'minItems':1}}}
        for name in ('event_refs','evidence_refs')]})
    kind: Literal['observed']

    @model_validator(mode='after')
    def has_direct_evidence(self):
        if not (self.event_refs or self.evidence_refs):
            raise ValueError('observed 填写支持本条答复的已读 event_refs 或 evidence_refs')
        return self


class WorkAnswerBasis(EvidenceAnswerBasis):
    kind: Literal['work_result']


class MixedAnswerBasis(EvidenceAnswerBasis):
    kind: Literal['mixed']


class UnverifiedAnswerBasis(EvidenceAnswerBasis):
    kind: Literal['unverified']
    unresolved: list[AnswerGap]=Field(min_length=1,max_length=12,
        description='本条答复仍待核实的具体内容')


TurnAnswerBasis = Annotated[
    SocialAnswerBasis | ObservedAnswerBasis | WorkAnswerBasis | MixedAnswerBasis | UnverifiedAnswerBasis,
    Field(discriminator='kind')]


_RELATIONS=('ack_ref','operation_ref','delivery_ref','work_ref')
_FILE_TEXT_FIELDS=('segments','reply_to','expect_reply','addressed_to','covers')


def _message_shapes():
    shapes=[]
    for relation in (None,*_RELATIONS):
        forbidden=['file_asset_id',*(name for name in _RELATIONS if name!=relation)]
        if relation:
            forbidden.append('covers')
        shapes.append({'required':['segments',relation or 'source'],
            'properties':{relation or 'source':{'type':'string','minLength':1}},
            'not':{'anyOf':[{'required':[name]} for name in forbidden]}})
    for relation in ('delivery_ref','work_ref'):
        forbidden=[*_FILE_TEXT_FIELDS,*(name for name in _RELATIONS if name!=relation)]
        shapes.append({'required':['file_asset_id',relation],
            'properties':{name:{'type':'string','minLength':1} for name in ('file_asset_id',relation)},
            'not':{'anyOf':[{'required':[name]} for name in forbidden]}})
    return {'oneOf':shapes}


class TurnMessage(StrictModel):
    model_config=ConfigDict(json_schema_extra=_message_shapes())
    segments:list[TurnPart]=Field(default_factory=list,min_length=1,max_length=12)
    file_asset_id:str|None=Field(default=None,min_length=1,description="prepare_workspace_file 返回且已审查的资产 ID；必须独占此条并以 delivery_ref/work_ref 绑定原工作，不含文字通知")
    reply_to: str|None=Field(default=None,description='可选消息M引用')
    source: str|None=Field(default=None,min_length=1,description='本条回应对应的已读来源M；普通聊天和操作确认使用人类原话。delivery_ref交付可省略，沿用事项原始委托；插件系统来源保留原类型，与显示引用reply_to分别表达')
    covers:list[str]=Field(default_factory=list,max_length=16,
        description='普通回复同时回答的其他来源M；逐项为本轮已完整读取、待处理或此前checkpoint继续处理的人类原话。主来源只填source；此列表不改变请求者或工作归属')
    ack_ref: str|None=Field(default=None,min_length=1,description='复制本轮start_work/schedule_reminder回执中的ack_ref')
    operation_ref: str|None=Field(default=None,min_length=1,description='复制本轮控制工作、提醒或记忆操作返回的proposal_ref；只确认这项操作实际提交后的结果')
    delivery_ref: str|None=Field(default=None,min_length=1,description='本条送达后完成的工作J或提醒T；可省略source沿用已读原委托。宿主关联本次已读到期或完成来源。工作只接受completed/partial执行结果')
    work_ref: str|None=Field(default=None,min_length=1,description='本条进展或结果所依据的工作J；首次待交付结果使用delivery_ref')
    expect_reply: ReplyExpectation|None=None
    addressed_to:list[str]=Field(default_factory=list,description='实际对谁说话的成员U引用；与source、reply_to和期待回答者分别填写')
    answer_basis: TurnAnswerBasis|None=Field(default=None,
        description='资料性答复填写；闲聊可省略。social/general只填类别；observed填已读原话或资料，work_result绑定真实工作成果，mixed组合依据，unverified列明缺口。此信息不发送到群')

    @model_validator(mode='after')
    def one_message_relation(self):
        selected=[name for name in _RELATIONS if getattr(self,name) is not None]
        if self.file_asset_id:
            if self.segments or self.reply_to or self.expect_reply or self.addressed_to:
                raise ValueError('文件上传独占一条行动并绑定原工作；文字通知另行提交')
            if self.ack_ref or self.operation_ref:
                raise ValueError('文件行动不能同时确认创建或操作')
            if selected not in (['delivery_ref'], ['work_ref']):
                raise ValueError('文件行动必须且只能绑定一个 delivery_ref 或 work_ref')
        else:
            if not self.segments:
                raise ValueError('普通消息需要至少一个片段')
            if len(selected)>1:
                raise ValueError('ack_ref、operation_ref、delivery_ref、work_ref每条消息只能选择一种；创建、操作确认、结果交付和普通工作引用分别表达')
            if not selected and not self.source:
                raise ValueError('普通回复的 source 填写本条实际回应的已读来源M')
        if self.covers and (selected or self.file_asset_id):
            raise ValueError('covers 只用于普通回复；创建、控制、工作和文件沿各自唯一业务来源处理')
        if len(set(self.covers))!=len(self.covers) or self.source in self.covers:
            raise ValueError('covers 只列其他来源，每条出现一次，主来源保留在 source')
        if isinstance(self.answer_basis,(WorkAnswerBasis,MixedAnswerBasis)):
            direct=bool(self.answer_basis.event_refs or self.answer_basis.evidence_refs)
            if (isinstance(self.answer_basis,WorkAnswerBasis) or not direct) and not (self.work_ref or self.delivery_ref):
                raise ValueError('answer_basis 的工作成果须关联 work_ref 或 delivery_ref；直接资料使用 observed，mixed 至少保留一项真实依据')
        return self

class SourceResolution(StrictModel):
    source:str=Field(description='本次处理的已读来源M，包括人类原话及到期、工作完成等系统来源，保留原类型；继续同一请求可沿用此前checkpoint来源，与messages[].source的回应来源分开')
    status:Literal['incomplete','silent']
    reason:str=Field(default='',max_length=500)
    unfinished:list[str]=Field(default_factory=list,description='同一原话中仍未完成的要求；未做的部分不能被已发送内容覆盖')


class ObservationChoice(StrictModel):
    source: str
    action: Literal['continue', 'end']


class Respond(StrictModel):
    messages:list[TurnMessage]=Field(max_length=3,description='零至三条；空列表表示沉默')
    sources:list[SourceResolution]=Field(default_factory=list,
        description='仅声明旁听或未完成部分并说明原因；消息source/covers、实际创建提案及真实等待的处理结果由宿主生成。仅仅读到的独立来源保持待处理')
    next:Literal['end','continue','wait']
    note:str=Field(default='',max_length=500,description='内部参与判断；尚有待处理来源但本次不处理任何来源时，说明等待条件或结束原因。不发送、不保存为长期认识')
    release_focus:list[str]=Field(default_factory=list,description='根据本人原话停止本次误接或互动的成员U；只撤销现有关注窗口，不写长期规则')
    observation: ObservationChoice | None = None

class Evidence(StrictModel):
    evidence:list[str]=Field(min_length=1,description='本轮实际读过的消息M引用')

class StartWork(Evidence):
    request_source:str=Field(description='提出这项委托的已读人类消息M；据此确定请求者，其他资料填evidence')
    goal:str=Field(min_length=1,description='保留原问题指定的公司、型号、对象与所求结果；未核实的同名猜测不替代目标')
    constraints:list[str]=Field(default_factory=list)
    result_refs:list[str]=Field(default_factory=list,description='复用本群已有资料R')
    reuse_work_ref:str|None=Field(default=None,min_length=1,
        description='对已读的普通研究完整或部分成果做后续整理／导出时填写原工作J；固定该结果版本并带入其资料，新工作保留本次request_source。专用插件沿所属入口，继续原未完成研究使用resume_work')

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
    'start_work':(StartWork,'建立需要较长执行、跨轮保存进度或使用仅供工作调用能力的后台只读工作。当前循环与剩余预算内可完成的短查询、计算和必要续读直接处理，分页本身不要求建工作。确需创建时先调用本工具，再把回执中的ack_ref复制到respond的确认消息，不自拟引用。'),
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
    # Increment this declaration revision when these fixed schemas or their
    # descriptions change. The per-call snapshot remains the actual evidence.
    return _RecordedToolDefinition(
        {'type':'function','function':{'name':name,'description':description,'parameters':model.model_json_schema()}},
        component_id=f'core.proposals.{name}', revision=1)

# The same fixed business models define the public schema and local parsing.
RESPOND=definition('respond',Respond,
    '提交剩余暂存提案及零至三条消息；空messages表示沉默，提案仍提交。'
    '普通回复填segments、source，可用covers合并回应其他已读来源。'
    '创建确认、操作确认、成果交付、工作说明分别选唯一ack_ref、operation_ref、delivery_ref或work_ref；'
    '文件填file_asset_id及唯一工作关系，不带segments。业务形状由字段确定。'
    'sources只说明silent/incomplete；replied、delegated及waiting由实际消息、创建提案与等待关系生成。'
    '引用取本轮事实与真实回执，宿主核对阅读、原请求者、工作修订、当前资格及真实送达关系。')


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
        # The caller installs the run's real count reader.  Until it does, a
        # ledger has no continuation allowance, which is the safe reading for
        # a ledger that was never attached to a run.
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
        # Current targets live in runtime facts and tool receipts, not enums.
        return copy.deepcopy(RESPOND)

    def definitions(self):
        return [definition(name,*value) for name,value in TOOLS.items()]

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
                reused=None
                result_ids=[refs.result_id(r) for r in model.result_refs]
                if model.reuse_work_ref:
                    previous=refs.job(model.reuse_work_ref)
                    if (previous['id'],previous['revision']) not in self.context.confirmed_work_results:
                        raise ValueError('reuse_work_ref 须先读取该工作当前版本的成果；仅工作目录不够，按 query_jobs 及其详情续读入口读取')
                    reused=ReusedWorkResult.from_job(previous)
                    result_ids=list(dict.fromkeys([*result_ids,*reused.result_ids]))
                collection='jobs'
                value=JobProposal(proposal_id=proposal_ref,goal=model.goal,constraints_add=model.constraints,
                    source_event_ids=list(dict.fromkeys([source.id,*evidence])),result_ids=result_ids,reused_work=reused,
                    requester_qq_uid=source.actor_id.removeprefix('user:'),request_source_event_id=source.id,
                    initiator=self.human_source(source))
            elif name in {'revise_work','cancel_work','resume_work'}:
                job=refs.job(model.work_ref)
                if name=='resume_work' and not job.get('can_resume'):
                    raise ValueError('该工作当前不能继续；先读取当前工作状态、剩余预算与恢复原因')
                if name!='resume_work' and job['status'] in {'completed','cancelled','shadow_observed','delivery_unknown'}:
                    raise ValueError('该工作当前不可修订或取消；不能用旧工作引用重新建立执行资格')
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
                task_id=refs.task_id(model.reminder_ref)
                if task_id not in refs.editable_tasks:
                    raise ValueError('该提醒不在当前可编辑目录；先读取当前状态，不能控制已终结事项')
                collection='tasks'
                value=TaskProposal(proposal_id=proposal_ref,operation='update' if name=='update_reminder' else 'cancel',
                    task_id=task_id,expected=refs.task_snapshots[task_id].model_copy(deep=True),
                    due_at=getattr(model,'due_at',None),description=getattr(model,'description',''),source_event_ids=evidence)
            elif name in {'remember','supersede_memory'}:
                memory_ids=[refs.memory_id(r) for r in getattr(model,'memory_refs',[])]
                if set(memory_ids)-refs.editable_memories:
                    raise ValueError('待修订认识尚未作为可编辑内容读取；先查询当前有效认识及其来源')
                collection='memories'
                value=MemoryProposal(proposal_id=proposal_ref,operation='create' if name=='remember' else 'supersede',
                    subject=refs.actor_id(model.subject),kind=model.kind,statement=model.statement,basis='reported',
                    evidence=evidence,expires_at=model.expires_at,scope=refs.scene_id,reason=getattr(model,'reason',''),
                    target_memory_ids=memory_ids)
            elif name=='refute_memory':
                memory_id=refs.memory_id(model.memory_ref)
                if memory_id not in refs.editable_memories:
                    raise ValueError('待撤销认识尚未作为可编辑内容读取；先查询当前有效认识及其来源')
                collection='memories'
                value=MemoryProposal(proposal_id=proposal_ref,operation='refute',target_memory_ids=[memory_id],
                    reason=model.reason,evidence=evidence,scope=refs.scene_id)
            elif name=='resolve_wait':
                collection='loops';value=refs.loop_id(model.wait_ref)
                if value not in refs.active_loops:
                    raise ValueError('该等待不在本轮活动目录；不能通过旧引用结束未确认的等待')
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

    async def finish(self,arguments,*,result_reads=None,resolve_evidence_refs=None):
        error_path='respond'
        try:
            result=Respond.model_validate_json(json.dumps(arguments,ensure_ascii=False),strict=True)
            refs=self.context.refs;messages=[]
            if len(result.messages)+self.messages_committed>3:
                raise ValueError('所有checkpoint共用本轮三条消息上限')
            if result.next!='end' and self.remaining_model_calls()==0:
                raise ValueError('原执行预算不足以继续或恢复等待，请结束并保留未完成项')
            pending=self.plugin_source_ids or {wake.event_id for wake in self.context.session.pending_wakes}
            available=(pending|self.continuing_sources)&refs.read_events
            handled=[]
            for index,item in enumerate(result.sources):
                error_path=f'sources[{index}].source'
                handled.append(refs.event_id(item.source))
            if len(handled) != len(set(handled)) or not set(handled).issubset(available):
                raise ValueError('sources只能填写本轮已读、当前待处理或本轮此前checkpoint已处理的来源，每个来源只能出现一次')
            declarations=dict(zip(handled,result.sources))
            source_records={event.id:event for event in await self.context.runtime.event_store.events_by_ids(
                refs.scene_id, sorted(available), refs.cutoff)}
            if set(handled)-source_records.keys():
                raise ValueError('来源不在当前场景与读取截点内')
            source_candidates=[source_records[ident] for ident in handled
                if ident in self.plugin_source_ids or human_event_uid(source_records[ident]) is not None
                and source_records[ident].actor_id!=refs.bot_actor_id]
            error_path='proposals'
            controlled_jobs=[proposal.job_id for proposal in self.jobs if proposal.operation!='create']
            controlled_tasks=[proposal.task_id for proposal in self.tasks if proposal.operation!='create']
            changed_memories=[ident for proposal in self.memories for ident in proposal.target_memory_ids]
            if any(len(values)!=len(set(values)) for values in (controlled_jobs,controlled_tasks,changed_memories)):
                raise ValueError('同一轮对同一工作、提醒或认识目标只能保留一项操作；用discard_proposal撤回重复或冲突提案')
            confirmed_operations=set()
            for message_index,item in enumerate(result.messages):
                message_path=f'messages[{message_index}]'
                error_path=message_path+'.ack_ref'
                if item.ack_ref and item.ack_ref not in self.proposal_refs:
                    raise ValueError('ack_ref没有对应本轮提案。当前已暂存的新建事项引用：'
                        + ', '.join(sorted(self.proposal_refs)) + '。引用字段本身不会创建工作；'
                        '需要新建工作或提醒时先调用对应工具取得staged回执，再用返回的ack_ref确认；普通短查询不填写ack_ref。')
                operation=None
                operation_sources=[]
                error_path=message_path+'.operation_ref'
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
                for part_index,part in enumerate(item.segments):
                    error_path=f'{message_path}.segments[{part_index}]'
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
                error_path=message_path+'.reply_to'
                if item.reply_to:
                    event_id=refs.event_id(item.reply_to)
                    rows=await self.context.runtime.event_store.read_context(event_id,before=0,after=0,
                        allowed_scopes=[refs.scene_id],through_rowid=refs.cutoff)
                    reply=rows[0]['payload'].get('message_id') if rows else None
                    if reply is None:raise ValueError('引用消息没有可回复的协议message_id')
                    reply=str(reply)
                error_path=message_path+('.operation_ref' if operation else '.work_ref')
                job=(refs.job(operation[1].job_id) if operation and operation[0]=='jobs'
                     else refs.job(item.work_ref) if item.work_ref else None)
                delivery=None
                error_path=message_path+'.delivery_ref'
                if item.delivery_ref:
                    if item.delivery_ref in refs.tasks or item.delivery_ref in refs.tasks.values():
                        delivery=refs.task_id(item.delivery_ref)
                        task=await self.context.runtime.event_store.get_task(delivery)
                        if (delivery not in refs.deliverable_tasks or task is None or task.scene_id != refs.scene_id
                                or not task_delivery_available(task.status, task.payload)):
                            state=task.status.value if task is not None and task.scene_id == refs.scene_id else 'unavailable'
                            raise ValueError(f'提醒{item.delivery_ref}当前不可交付（{state}）；不能填写delivery_ref。待核对事项保留未完成原因，有明确人类要求时重新安排或取消；其他独立请求可继续提交')
                    else:
                        target=refs.job(item.delivery_ref)
                        if (target['status']!='result_ready' or target['execution_status'] not in {'completed','partial'}
                                or target['delivery_action_id'] is not None):
                            raise ValueError('该工作当前没有可首次交付的完整或部分结果；状态说明用work_ref，不能重复履约')
                        if job and job['id']!=target['id']:raise ValueError('履约与工作引用不一致')
                        job=target;delivery=job['id']
                if item.file_asset_id:
                    error_path=message_path+'.file_asset_id'
                    if not refs.scene_id.startswith('group:'):
                        raise ValueError('文件上传只用于群聊')
                    info=refs.file_assets.get(item.file_asset_id)
                    if info is None or item.file_asset_id not in refs.deliverable_file_ids():
                        raise ValueError('file_asset_id 不是本轮当前修订中已审查、未过期且没有已提交或未知上传行动的文件')
                    if job is None or job['id']!=info['job_id'] or job['revision']!=info['job_revision']:
                        raise ValueError('文件必须绑定其所属工作的当前修订')
                error_path=message_path
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
                error_path=message_path+'.source'
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
                covered=[]
                for cover_index,reference in enumerate(item.covers):
                    error_path=f'{message_path}.covers[{cover_index}]'
                    ident=refs.event_id(reference)
                    if ident not in available:
                        raise ValueError('覆盖来源须是本轮待处理或此前checkpoint继续处理的已读原话')
                    if ident==source.id or ident in covered:
                        raise ValueError('同条消息只覆盖每个其他来源一次')
                    await self.request_event(ident)
                    covered.append(ident)
                # An explicit follow-up may come from another participant.
                # Message ownership follows that human source; the referenced
                # work keeps its own original requester and revision.
                expectation=item.expect_reply
                error_path=message_path+'.addressed_to'
                addressed=list(dict.fromkeys(refs.member_id(ref) for ref in item.addressed_to))
                error_path=message_path+'.expect_reply'
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
                basis=None
                error_path=message_path+'.answer_basis'
                if item.answer_basis is not None:
                    declared=item.answer_basis
                    direct=declared if isinstance(declared,EvidenceAnswerBasis) else None
                    evidence=[]
                    if direct and direct.evidence_refs:
                        if resolve_evidence_refs is None:
                            raise ValueError('当前入口没有已确认资料页引用；先沿原读取入口取得正文')
                        evidence=resolve_evidence_refs(direct.evidence_refs)
                    basis=AnswerBasis(kind=declared.kind,
                        event_ids=[refs.event_id(ref) for ref in direct.event_refs] if direct else [],
                        result_spans=evidence,
                        unresolved=direct.unresolved if direct else [],
                        work_result=(AnswerWorkResult.from_job(job) if job and declared.kind in {'work_result','mixed'} else None))
                    if basis.work_result and (job['id'],job['revision']) not in self.context.confirmed_work_results:
                        raise ValueError('该工作结果尚未实际提供；先读取当前工作详情，目录位置不是结果正文')
                    await self.context.runtime.event_store.validate_answer_basis(basis,refs.scene_id,
                        through_rowid=refs.cutoff,read_event_ids=refs.read_events,result_reads=result_reads or {},
                        bot_actor_id=refs.bot_actor_id)
                error_path=message_path
                messages.append(MessageProposal(segments=parts,file_asset_id=item.file_asset_id,reply_to=reply,task_ref=item.ack_ref,operation_ref=item.operation_ref,fulfils_task_id=delivery,
                    source_event_id=source.id,requester_qq_uid=requester,
                    covered_source_event_ids=covered,
                    plugin_origin=message_owner,
                    addressed_to=addressed,
                    answer_basis=basis,
                    job_id=job['id'] if job else None,job_revision=job['revision'] if job else None,
                    expect_reply=bool(expectation),reply_target=refs.member_id(expectation.target) if expectation else None,
                    reply_intent=expectation.intent if expectation else None))
            error_path='sources'
            for message in messages:
                for ident in [message.source_event_id,*message.covered_source_event_ids]:
                    if ident in available and ident not in handled:
                        handled.append(ident)
            for proposal in [*self.jobs,*self.tasks]:
                ident=proposal.request_source_event_id
                if proposal.operation=='create' and ident in available and ident not in handled:
                    handled.append(ident)
            for ident,event in source_records.items():
                if (ident not in handled and event.event_type.value not in {'GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED'}
                        and any(self._message_handles_source(message,event) for message in messages)):
                    handled.append(ident)
            if pending and not handled and not result.note.strip():
                raise ValueError('没有实际处理待处理来源；在note说明等待依赖或本次结束原因')
            outcomes=[]
            for ident in handled:
                item=declarations.get(ident)
                error_path=(f'sources[{result.sources.index(item)}]' if item else 'sources(派生)')
                original=source_records[ident]
                related_indices=[index for index,message in enumerate(messages)
                    if self._message_handles_source(message,original)]
                related_messages=[messages[index] for index in related_indices]
                related_proposals=[ref for ref,(kind,proposal) in self.staged.items()
                    if (kind=='memories' and ident in proposal.evidence
                        or kind in {'jobs','tasks'} and (proposal.request_source_event_id==ident or ident in proposal.source_event_ids))]
                if item and not (item.reason.strip() or item.unfinished):
                    raise ValueError('未完成或旁听须说明原因或未完成范围')
                if item and item.status=='silent' and (related_messages or related_proposals):
                    raise ValueError(f'sources中的{item.source}已关联消息或实际操作，不能标为silent；到期或完成事件也会通过delivery_ref关联交付消息')
                if item:
                    status=item.status
                elif result.next=='wait' and any(message.expect_reply for message in related_messages):
                    status='waiting'
                elif any(kind in {'jobs','tasks'} and proposal.operation=='create'
                        and proposal.request_source_event_id==ident for kind,proposal in self.staged.values()):
                    status='delegated'
                elif related_messages:
                    status='replied'
                else:
                    raise ValueError('来源没有实际消息或新建事项；使用sources声明未完成范围或旁听原因')
                outcomes.append(SourceOutcome(source_event_id=ident,status=status,reason=item.reason if item else '',
                    unfinished=item.unfinished if item else [],proposal_refs=related_proposals,message_indices=related_indices))
            error_path='next'
            if result.next=='wait' and (sum(message.expect_reply for message in messages)!=1
                    or not any(messages[index].expect_reply for source in outcomes for index in source.message_indices)
                    or self.messages_committed+len(messages)>=3):
                raise ValueError('wait需要且只能有一个expect_reply消息、明确关联的处理来源及后续表达额度；未完成部分可在sources保留')
            error_path='sources'
            unlinked={ref for ref,(kind,_) in self.staged.items() if kind!='loops'}-{ref for source in outcomes for ref in source.proposal_refs}
            if unlinked:
                raise ValueError('实际提案缺少来源处理结果；用操作确认消息绑定其原话，或在sources保留未完成范围：'+', '.join(sorted(unlinked)))
            source_candidates=[source_records[ident] for ident in handled
                if ident in self.plugin_source_ids or human_event_uid(source_records[ident]) is not None
                and source_records[ident].actor_id!=refs.bot_actor_id]
            error_path='release_focus'
            released=list(dict.fromkeys(refs.member_id(ref) for ref in result.release_focus))
            if set(released)-{event.actor_id for event in source_candidates}:
                raise ValueError('撤销关注必须有本次处理的本人原话，不能替其他人结束互动')
            observation = None
            error_path='observation'
            if result.observation:
                from len_bot.cognition.models import ObservationDecision
                ident = refs.event_id(result.observation.source)
                if (self.plugin_source_ids or ident not in handled or ident in self.continuing_sources
                        or not any(event.id == ident and event.event_type in HUMAN_INPUTS for event in source_candidates)):
                    raise ValueError('观察期决定必须引用本次新处理的人类原话；插件来源和旧阶段不能续期')
                observation = ObservationDecision(source_event_id=ident, action=result.observation.action)
            return EpisodeOutcome(disposition=FinalDisposition.ACTION if messages else FinalDisposition.SILENCE,
                decision_reason=result.note or ('参与' if messages else '旁听'),message_proposals=messages,
                source_outcomes=outcomes,checkpoint_index=self.checkpoint_index,next_action=result.next,
                release_focus_actor_ids=released, observation=observation,
                task_proposals=self.tasks,job_proposals=self.jobs,memory_proposals=self.memories,resolve_open_loop_ids=self.loops)
        except TerminalArgumentError:
            raise
        except ValidationError as error:
            details=[]
            for issue in error.errors(include_url=False,include_input=False):
                path=''.join(f'[{part}]' if isinstance(part,int) else '.'+str(part) for part in issue['loc']).lstrip('.')
                location=path if error_path=='respond' else error_path+('.'+path if path else '')
                details.append(f"{location or 'respond'}: {issue['msg']}")
            raise TerminalArgumentError('; '.join(details)) from error
        except (ValueError,KeyError) as error:
            raise TerminalArgumentError(f'{error_path}: {error}') from error

    @staticmethod
    def _message_handles_source(message,event):
        if event.id==message.source_event_id or event.id in message.covered_source_event_ids:
            return True
        return event.event_type in RUNTIME_INPUTS and bool(
            message.job_id and message.job_id==event.payload.get('job_id')
            or message.fulfils_task_id and message.fulfils_task_id==event.payload.get('task_id')
            or message.source_event_id and message.source_event_id==event.payload.get('origin_event_id'))

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
