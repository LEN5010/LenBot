from enum import StrEnum
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
import uuid
import time

class MemoryCertainty(StrEnum):
    TENTATIVE = "tentative"
    LIKELY = "likely"
    STRONG = "strong"
    EXPLICIT = "explicit"

class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REFUTED = "refuted"
    FORGOTTEN = "forgotten"

class MemoryKind(StrEnum):
    """Typed social memory slots (ADR-0019 §10.2).

    Deliberately narrow: evidence-backed beliefs about people, groups, and the
    bot's own social position. No personality-profile labels.
    """
    PREFERENCE = "preference"
    HABIT = "habit"
    RELATIONSHIP = "relationship"
    FACT = "fact"
    GROUP_NORM = "group_norm"
    TOPIC_INTEREST = "topic_interest"
    RECURRING_ROLE = "recurring_role"
    SOCIAL_PATTERN = "social_pattern"

class EpisodeRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"ep_rec_{uuid.uuid4().hex[:10]}")
    scene_id: str
    title: str
    summary: str
    source_event_ids: list[str]
    participants: list[str]
    tags: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:10]}")
    subject: str
    kind: MemoryKind
    key: str
    value: str
    temporal: str = "recent"  # recent, persistent, historical
    certainty: MemoryCertainty = MemoryCertainty.LIKELY
    scope: str
    evidence: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: Optional[str] = None
    revision_reason: str = ""
    revision_evidence: list[str] = Field(default_factory=list)
    access_count: int = 0
    last_accessed_at: Optional[float] = None
    decay_score: float = 1.0
    human_readable_assertion: str
    created_at: float = Field(default_factory=time.time)
    last_confirmed_at: float = Field(default_factory=time.time)

class MemoryChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["upsert", "refute", "supersede"] = "upsert"
    target_memory_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    subject: str = ""
    kind: MemoryKind = MemoryKind.FACT
    key: str = ""
    value: str = ""
    temporal: str = "recent"
    certainty: MemoryCertainty = MemoryCertainty.TENTATIVE
    evidence: list[str]
    human_readable_assertion: str = ""

    @model_validator(mode="after")
    def revision_shape(self):
        if self.operation == "upsert" and self.target_memory_ids:
            raise ValueError("Use supersede or refute to revise memory IDs")
        if self.operation != "upsert" and (not self.target_memory_ids or not self.reason.strip()):
            raise ValueError("Memory revision requires target_memory_ids and reason")
        if self.operation == "refute" and len(self.target_memory_ids) != 1:
            raise ValueError("Each refute proposal targets one memory")
        if len(set(self.target_memory_ids)) != len(self.target_memory_ids):
            raise ValueError("Duplicate memory revision targets")
        return self


class MemoryProposal(MemoryChange):
    scope: str = Field(default="", description="Scope injected authoritatively by runtime")
