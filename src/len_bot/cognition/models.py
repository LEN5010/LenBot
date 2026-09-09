from enum import StrEnum
from typing import Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from len_bot.cognition.jobs import JobProposal
from len_bot.media.models import MessageSegment, segment_text
from len_bot.cognition.providers import ModelProfile
from len_bot.events.models import PluginOrigin
from len_bot.plugins.agent import PluginAgentRequest

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
    model_calls_limit: int = Field(ge=1)
    tool_calls_limit: int = Field(ge=0)
    model_calls_used: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    context_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0)
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


class MessageProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[MessageSegment] = Field(min_length=1)
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
    requester_qq_uid: str | None = None
    addressed_to: list[str] = Field(default_factory=list, description="Actual addressed member actor IDs, separate from source and quote")
    plugin_origin: PluginOrigin | None = None

    @model_validator(mode="after")
    def validate_body(self):
        if not self.content.strip():
            raise ValueError("A message needs nonempty text or image segments")
        if sum(value is not None for value in (self.task_ref, self.operation_ref, self.fulfils_task_id)) > 1:
            raise ValueError("Creation acknowledgement, operation confirmation and fulfilment are separate message relations")
        if (self.job_id is None) != (self.job_revision is None):
            raise ValueError("A job message needs its actual work ID and observed revision together")
        return self

    @property
    def content(self) -> str:
        return segment_text(self.segments)

class TaskProposal(BaseModel):
    operation: str = "create"
    task_id: str | None = None
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

    @property
    def handled_source_event_ids(self):
        return [item.source_event_id for item in self.source_outcomes]

    def requires_fresh_input(self) -> bool:
        return bool(self.task_proposals or self.job_proposals or self.memory_proposals
                    or self.resolve_open_loop_ids or self.message_proposals or self.release_focus_actor_ids)
