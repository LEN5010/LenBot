from enum import StrEnum
from typing import Any
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
    kind: str  # preference, relationship, fact, pattern
    key: str
    value: str
    temporal: str = "recent"  # recent, persistent, historical
    certainty: MemoryCertainty = MemoryCertainty.LIKELY
    scope: str
    visibility: str = "scene"
    evidence: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    human_readable_assertion: str
    created_at: float = Field(default_factory=time.time)
    last_confirmed_at: float = Field(default_factory=time.time)

class MemoryProposal(BaseModel):
    subject: str
    kind: str
    key: str
    value: str
    temporal: str = "recent"
    certainty: MemoryCertainty = MemoryCertainty.LIKELY
    scope: str
    visibility: str = "scene"
    evidence: list[str]
    human_readable_assertion: str
