"""Information-work proposal contract. Execution never owns social authority."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from len_bot.events.models import PluginOrigin


class JobProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["create", "revise", "cancel", "resume"] = "create"
    proposal_id: str | None = None
    job_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    goal: str | None = None
    constraints_add: list[str] = Field(default_factory=list)
    constraints_remove: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    requester_qq_uid: str | None = None
    request_source_event_id: str | None = None
    work_operation: str = Field(default='information',min_length=1)
    work_parameters: dict | None = None
    plugin_origin: PluginOrigin | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if self.goal is not None and not self.goal.strip():
            raise ValueError("Job goal cannot be blank")
        if self.operation == "create":
            if not self.request_source_event_id or not self.requester_qq_uid:
                raise ValueError("Job creation needs an explicit human request source and its requester")
            if self.request_source_event_id not in self.source_event_ids:
                raise ValueError("The request source must be part of the supplied original evidence")
            if not self.proposal_id or not self.goal or not self.goal.strip() or self.job_id:
                raise ValueError("Job creation needs proposal_id and goal, not job_id")
            if self.work_operation!='information' and (self.plugin_origin is None or self.work_parameters is None):
                raise ValueError('Specialized work requires its plugin owner and parameters')
            if self.work_operation=='information' and self.work_parameters is not None:
                raise ValueError('Ordinary information work has no specialized parameters')
        elif not self.job_id or self.expected_revision is None:
            raise ValueError("Job control needs real job_id and expected_revision")
        if self.operation=='resume' and (self.goal is not None or self.constraints_add or self.constraints_remove or self.work_parameters is not None):
            raise ValueError('Resume preserves the existing goal and scope; changing them requires revise')
        return self


class ResultSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result_id: str = Field(min_length=1)
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)
    coordinate_unit: Literal['characters', 'records']

    @model_validator(mode='after')
    def ordered_span(self):
        if self.start > self.end:
            raise ValueError('A result span must satisfy start <= end')
        return self


class ResultPresentation(ResultSpan):
    name: str | None = None
    total: int = Field(ge=0, strict=True)

    @model_validator(mode='after')
    def bounded_span(self):
        if self.end > self.total:
            raise ValueError('A presented span must be within its original observation')
        return self


class CompletedWorkStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(min_length=1, max_length=600)
    result_ids: list[str] = Field(min_length=1, max_length=12)
    evidence_spans: list[ResultSpan] = Field(default_factory=list)


class WorkState(BaseModel):
    """A concise projection, never a second goal, budget or delivery ledger."""
    model_config = ConfigDict(extra="forbid")
    goal_revision: int = Field(ge=1)
    plan: list[str] = Field(default_factory=list, max_length=12)
    completed_steps: list[CompletedWorkStep] = Field(default_factory=list, max_length=12)
    key_result_ids: list[str] = Field(default_factory=list, max_length=24)
    evidence_spans: list[ResultSpan] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list, max_length=12)
    next_step: str = Field(default="", max_length=600)


class JobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "partial", "failed", "interrupted", "cancelled"]
    summary: str = Field(max_length=4000, description="最终结论、简短完整依据与适用条件；省去草稿和已放弃的推理，未解决的矛盾放入 unresolved")
    result_ids: list[str] = Field(default_factory=list)
    evidence_spans: list[ResultSpan] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    work_state: WorkState | None = None
    reason: str | None = None


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=100)
    lesson: str = Field(min_length=1, max_length=2000)
    result_ids: list[str] = Field(min_length=1, max_length=12)
    correction_event_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def revision_pair(self):
        if (self.skill_id is None) != (self.expected_version is None):
            raise ValueError("Skill revision needs skill_id and expected_version together")
        return self


class JobChanged(RuntimeError):
    pass


class JobResultRejected(ValueError):
    """Result validation failed inside a transaction that was rolled back."""


class JobBudgetExhausted(RuntimeError):
    def __init__(self,message,*,budget_kind='model_steps'):
        super().__init__(message)
        self.budget_kind=budget_kind
