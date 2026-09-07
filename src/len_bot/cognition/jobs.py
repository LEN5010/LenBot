"""Information-work proposal contract. Execution never owns social authority."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def validate_operation(self):
        if self.goal is not None and not self.goal.strip():
            raise ValueError("Job goal cannot be blank")
        if self.operation == "create":
            if not self.proposal_id or not self.goal or not self.goal.strip() or self.job_id:
                raise ValueError("Job creation needs proposal_id and goal, not job_id")
        elif not self.job_id or self.expected_revision is None:
            raise ValueError("Job control needs real job_id and expected_revision")
        return self


class JobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "partial", "failed", "interrupted", "cancelled"]
    summary: str = Field(max_length=4000, description="最终结论、简短完整依据与适用条件；省去草稿和已放弃的推理，未解决的矛盾放入 unresolved")
    result_ids: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class CompletedWorkStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str = Field(min_length=1, max_length=600)
    result_ids: list[str] = Field(min_length=1, max_length=12)


class WorkState(BaseModel):
    """A concise projection, never a second goal, budget or delivery ledger."""
    model_config = ConfigDict(extra="forbid")
    goal_revision: int = Field(ge=1)
    plan: list[str] = Field(default_factory=list, max_length=12)
    completed_steps: list[CompletedWorkStep] = Field(default_factory=list, max_length=12)
    key_result_ids: list[str] = Field(default_factory=list, max_length=24)
    unresolved: list[str] = Field(default_factory=list, max_length=12)
    next_step: str = Field(default="", max_length=600)


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


class JobBudgetExhausted(RuntimeError):
    pass
