from enum import StrEnum
from typing import Any, Optional
from pydantic import BaseModel, Field
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
    visibility: str = "scene"  # scene, global
    evidence: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    superseded_by: Optional[str] = None
    access_count: int = 0
    last_accessed_at: Optional[float] = None
    decay_score: float = 1.0
    human_readable_assertion: str
    created_at: float = Field(default_factory=time.time)
    last_confirmed_at: float = Field(default_factory=time.time)

class MemoryProposal(BaseModel):
    subject: str
    kind: MemoryKind
    key: str
    value: str
    temporal: str = "recent"
    certainty: MemoryCertainty = MemoryCertainty.LIKELY
    scope: str = Field(default="", description="Scope injected authoritatively by runtime")
    visibility: str = "scene"
    evidence: list[str]
    human_readable_assertion: str
