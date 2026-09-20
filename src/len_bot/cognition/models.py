from enum import StrEnum
from typing import Annotated, Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from len_bot.cognition.jobs import JobProposal, ResultSpan
from len_bot.media.models import MessageSegment, segment_text
from len_bot.cognition.providers import ModelProfile
from len_bot.events.models import PluginOrigin
from len_bot.plugins.agent import PluginAgentRequest
from len_bot.scheduler.models import ReminderControlSnapshot

class FinalDisposition(StrEnum):
    SILENCE = "SILENCE"
    ACTION = "ACTION"


class SourceOutcome(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_event_id: str
    status: Literal['replied','delegated','waiting','incomplete','silent']
    reason: str = ''
    unfinished: list[str] = Field(default_factory=list)
    proposal_refs: list[str] = Field(default_factory=list)
    message_indices: list[int] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


class ConversationResume(BaseModel):
    """A sent wait retains its request and budget, not a provider trajectory."""
    model_config = ConfigDict(extra='forbid')
    episode_id: str
    runtime_started_at: float
    model_profile: ModelProfile
    model_calls_limit: int | None = Field(default=None, ge=1)
    tool_calls_limit: int | None = Field(default=None, ge=0)
    model_calls_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    context_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0)
    elapsed_seconds_limit: float | None = Field(default=None, gt=0,
        description='本轮对话自首次模型调用起的绝对期限（秒）；恢复不重置')
    deadline_at: float | None = Field(default=None, gt=0,
        description='本轮对话窗口关闭的绝对 Unix 时刻；等待经过的时间同样计入，恢复对着同一时刻')
    messages_committed: int = Field(ge=0,le=3)
    next_checkpoint: int = Field(ge=0)
    next_proposal_handle: int = Field(ge=1)
    source_event_ids: list[str]
    result_ids: list[str]
    plugin_origin: PluginOrigin | None = None
    plugin_request: PluginAgentRequest | None = None


class OperationReceipt(BaseModel):
    """The committed result behind one turn-local control confirmation."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["committed"] = "committed"
    proposal_ref: str = Field(min_length=1)
    kind: Literal["work", "reminder", "memory"]
    operation: Literal["revise", "resume", "cancel", "update", "create", "refute", "supersede"]
    target_id: str = Field(min_length=1)
    revision: int | None = Field(default=None, ge=1)
    result_status: str = Field(min_length=1)
    source_event_ids: list[str] = Field(min_length=1)
    action_id: str | None = None
    reminder_due_at: float | None = None
    reminder_description: str | None = None

    @model_validator(mode="after")
    def target_version(self):
        allowed = {"work":{"revise","resume","cancel"}, "reminder":{"update","cancel"},
                   "memory":{"create","refute","supersede"}}
        if self.operation not in allowed[self.kind]:
            raise ValueError("Operation does not belong to its committed target kind")
        if self.kind in {"work", "memory"} and self.revision is None:
            raise ValueError("Work and memory operation receipts require the committed revision")
        if self.kind == "reminder" and (self.reminder_due_at is None or self.reminder_description is None):
            raise ValueError("Reminder operation receipts require the committed schedule and description")
        return self


AnswerBasisKind = Literal['social', 'general', 'observed', 'work_result', 'mixed', 'unverified']
AnswerGap = Annotated[str, Field(min_length=1, max_length=500)]


class AnswerWorkResult(BaseModel):
    """Host-resolved source links for the work revision used by one answer."""
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: Literal['completed', 'partial']
    result_ids: list[str] = Field(default_factory=list)
    evidence_spans: list[ResultSpan] = Field(default_factory=list)

    @classmethod
    def from_job(cls, job):
        result = job.get('result') or {}
        if result.get('status') not in {'completed', 'partial'}:
            raise ValueError('答复所引工作尚无完整或部分结果；进度或失败不能声明为工作成果依据')
        return cls(job_id=job['id'], revision=job['revision'], status=result['status'],
            result_ids=result.get('result_ids', []), evidence_spans=result.get('evidence_spans', []))


class AnswerBasis(BaseModel):
    """A declared basis with resolved identities, not a truth assessment."""
    model_config = ConfigDict(extra='forbid')
    kind: AnswerBasisKind
    event_ids: list[str] = Field(default_factory=list, max_length=16)
    result_spans: list[ResultSpan] = Field(default_factory=list, max_length=16)
    unresolved: list[AnswerGap] = Field(default_factory=list, max_length=12)
    work_result: AnswerWorkResult | None = None

    @model_validator(mode='after')
    def evidence_shape(self):
        direct = bool(self.event_ids or self.result_spans)
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError('答复依据不能重复引用同一原话')
        if any(span.end <= span.start for span in self.result_spans):
            raise ValueError('答复资料依据需要非空的实际展示区间')
        if self.kind == 'observed' and not direct:
            raise ValueError('observed 需要已读原话或资料范围，不能只填写依据类别')
        if self.kind == 'work_result' and self.work_result is None:
            raise ValueError('work_result 需要本条关联工作的真实成果')
        if self.kind == 'mixed' and not (direct or self.work_result):
            raise ValueError('mixed 需要至少一项原话、资料或工作成果关联')
        if self.work_result and self.kind not in {'work_result', 'mixed'}:
            raise ValueError('工作成果关系只能用于 work_result 或 mixed')
        if self.kind in {'social', 'general'} and direct:
            raise ValueError('带实际资料的答复应使用 observed 或 mixed，不标为无资料的社交/一般知识')
        if self.kind == 'unverified' and not self.unresolved:
            raise ValueError('unverified 需要说明当前答复具体未核实的内容')
        return self


class MessageProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[MessageSegment] = Field(default_factory=list)
    file_asset_id: str | None = None
    reply_to: Optional[str] = Field(default=None, description="OneBot message_id to quote-reply")
    expect_reply: bool = Field(default=False, description="Whether this message expects an answer from a specific user")
    reply_target: Optional[str] = Field(default=None, description="Actor ID expected to respond (e.g. user:123)")
    reply_intent: Optional[str] = Field(default=None, description="Topic or intent of expected answer")
    task_ref: str | None = None
    operation_ref: str | None = Field(default=None, min_length=1)
    fulfils_task_id: str | None = None
    job_id: str | None = None
    job_revision: int | None = None
    source_event_id: str | None = None
    covered_source_event_ids: list[str] = Field(default_factory=list, max_length=16,
        description='同一普通回复明确覆盖的其他已读人类来源；不改变主来源及请求者')
    requester_qq_uid: str | None = None
    addressed_to: list[str] = Field(default_factory=list, description="Actual addressed member actor IDs, separate from source and quote")
    plugin_origin: PluginOrigin | None = None
    answer_basis: AnswerBasis | None = None

    @model_validator(mode="after")
    def validate_body(self):
        if self.covered_source_event_ids:
            if (not self.source_event_id or self.file_asset_id or self.job_id or self.task_ref
                    or self.operation_ref or self.fulfils_task_id or self.plugin_origin):
                raise ValueError('其他来源覆盖只用于有明确主来源的普通人类回复')
            if (len(set(self.covered_source_event_ids)) != len(self.covered_source_event_ids)
                    or self.source_event_id in self.covered_source_event_ids):
                raise ValueError('其他来源覆盖不得重复或包含主来源')
        if self.file_asset_id and (self.segments or not self.job_id or self.reply_to or self.expect_reply
                or self.task_ref or self.operation_ref or self.addressed_to):
            raise ValueError('文件须单独作为本群工作交付，不混入消息片段或互动关系')
        if not self.content.strip():
            raise ValueError("A message needs nonempty text or image segments")
        if sum(value is not None for value in (self.task_ref, self.operation_ref, self.fulfils_task_id)) > 1:
            raise ValueError("Creation acknowledgement, operation confirmation and fulfilment are separate message relations")
        if (self.job_id is None) != (self.job_revision is None):
            raise ValueError("A job message needs its actual work ID and observed revision together")
        if self.answer_basis and self.answer_basis.work_result:
            work = self.answer_basis.work_result
            if (work.job_id, work.revision) != (self.job_id, self.job_revision) or self.operation_ref or self.task_ref:
                raise ValueError('答复工作依据必须对应本条实际工作版本，不能借创建或控制确认交付旧成果')
        return self

    @property
    def content(self) -> str:
        return "[文件资产 " + self.file_asset_id + "]" if self.file_asset_id else segment_text(self.segments)

class TaskProposal(BaseModel):
    operation: str = "create"
    task_id: str | None = None
    expected: ReminderControlSnapshot | None = None
    proposal_id: str | None = None
    due_at: float | None = None
    requester_id: str | None = None
    request_source_event_id: str | None = None
    target_actor_id: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)
    result: str | None = None
    description: str = ""
    delay_seconds: Optional[float] = Field(
        default=None,
        description="Seconds from now until task is due. Omit for condition-bound tasks."
    )
    # ADR-0018: condition-bound obligation. When set, the task fires when a committed
    # event of this type arrives in the scene (e.g. LIVE_STARTED), or at its deadline.
    wake_event_type: Optional[str] = Field(default=None)
    wake_match: Optional[dict[str, Any]] = Field(default=None, description="Exact dict match against event payload (ADR-0029, §16)")
    payload: dict[str, Any] = Field(default_factory=dict)
    origin_episode_id: Optional[str] = Field(default=None)
    origin_stimulus_id: Optional[str] = Field(default=None)
    origin_mode: str = Field(default="live")


# Deadline cap for condition-bound tasks that never see their wake event (ADR-0018).
CONDITION_TASK_DEFAULT_DEADLINE_SECONDS = 604800.0


from len_bot.memory.models import MemoryProposal

class WakeDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_event_id: str
    source_event_id: str
    decision: Literal['ask', 'confirm', 'decline', 'uncertain']


class ObservationDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_event_id: str
    action: Literal['continue', 'end']


class EpisodeOutcome(BaseModel):
    disposition: FinalDisposition = Field(
        default=FinalDisposition.SILENCE,
        description="Must be SILENCE if no message should be sent, or ACTION if sending message(s)"
    )
    decision_reason: str = Field(description="Brief structured reason explaining the decision (e.g. peer already answered)")
    message_proposals: list[MessageProposal] = Field(default_factory=list)
    task_proposals: list[TaskProposal] = Field(default_factory=list)
    job_proposals: list[JobProposal] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    resolve_open_loop_ids: list[str] = Field(default_factory=list)
    source_outcomes: list[SourceOutcome] = Field(default_factory=list)
    release_focus_actor_ids: list[str] = Field(default_factory=list)
    checkpoint_index: int = Field(default=0,ge=0)
    next_action: Literal['end','continue','wait'] = 'end'
    resume_state: ConversationResume | None = None
    wake_decision: WakeDecision | None = None
    observation: ObservationDecision | None = None

    @property
    def handled_source_event_ids(self):
        return [item.source_event_id for item in self.source_outcomes]

    def requires_fresh_input(self) -> bool:
        return bool(self.task_proposals or self.job_proposals or self.memory_proposals
                    or self.resolve_open_loop_ids or self.message_proposals or self.release_focus_actor_ids
                    or self.observation)
