"""One evidence-backed ledger for social knowledge, separate from execution facts."""

from enum import StrEnum
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MemoryKind(StrEnum):
    ADDRESS = "address"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    FACT = "fact"
    GROUP_NORM = "group_norm"


class MemoryBasis(StrEnum):
    REPORTED = "reported"
    INFERRED = "inferred"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REFUTED = "refuted"


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryItem(MemoryModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex}")
    scope: str
    subject: str
    kind: MemoryKind
    statement: str
    basis: MemoryBasis
    evidence: list[str]
    status: MemoryStatus = MemoryStatus.ACTIVE
    expires_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    revision: int = 1
    created_event_id: str | None = None
    revision_event_id: str | None = None
    supersedes_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    revision_reason: str = ""
    revision_evidence: list[str] = Field(default_factory=list)


class MemoryChange(MemoryModel):
    """A staged proposal. No write or authoritative scope comes from the model."""

    operation: Literal["create", "refute", "supersede"] = "create"
    subject: str = Field(default="", description="Scene participant actor ID, or this scene ID for group knowledge; never the Bot.")
    kind: MemoryKind = MemoryKind.FACT
    statement: str = Field(default="", description="What was reported or inferred, preserving who said it and relevant time.")
    basis: MemoryBasis = MemoryBasis.INFERRED
    evidence: list[str] = Field(min_length=1, description="Original event IDs actually read in this scene; summaries and Bot assertions are not independent evidence.")
    expires_at: float | None = None
    target_memory_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("subject", "statement", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence", "target_memory_ids")
    @classmethod
    def real_ids(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("Evidence and revision IDs must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("Evidence and revision IDs must be unique")
        return value

    @model_validator(mode="after")
    def proposal_shape(self):
        if self.operation == "create":
            if self.target_memory_ids:
                raise ValueError("create cannot revise an existing memory; use supersede")
        elif not self.target_memory_ids or not self.reason:
            raise ValueError("refute/supersede requires target_memory_ids and reason")
        if self.operation == "refute":
            if len(self.target_memory_ids) != 1:
                raise ValueError("refute targets exactly one memory")
            if self.statement:
                raise ValueError("refute does not create a replacement statement; use supersede")
        elif not self.subject or not self.statement:
            raise ValueError("create/supersede requires subject and statement")
        return self


class MemoryProposal(MemoryChange):
    scope: str = Field(default="", description="Injected by Runtime, never an authority granted to model output.")
