"""Event-derived session facts. Social interpretations belong to a single turn."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from len_bot.cognition.providers import ModelProfile


class OriginalCoverage(BaseModel):
    """Provided character ranges; this is observation progress, not a handled request."""
    model_config = ConfigDict(extra='forbid')
    total: int = Field(ge=0)
    ranges: list[tuple[int, int]] = Field(default_factory=list)

    @model_validator(mode='after')
    def normalize(self):
        merged = []
        for start, end in sorted(self.ranges):
            if not 0 <= start <= end <= self.total:
                raise ValueError('Original coverage is outside the message')
            if start == end and self.total:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self.ranges = merged
        return self

    def merged_with(self, other: 'OriginalCoverage') -> 'OriginalCoverage':
        if self.total != other.total:
            raise ValueError('Original message length changed')
        return OriginalCoverage(total=self.total, ranges=[*self.ranges, *other.ranges])

    @property
    def complete(self) -> bool:
        return self.ranges == [(0, self.total)]

    @property
    def next_offset(self) -> int:
        return self.ranges[0][1] if self.ranges and self.ranges[0][0] == 0 else 0


class ParticipantFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor_id: str
    nickname: str | None = None
    card: str | None = None
    role: str | None = None

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or self.actor_id


class PendingWake(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: str
    rowid: int = 0
    actor_id: str
    reasons: list[str]
    certain: bool
    # Opportunity-class wakes expire; a wake stored before this field existed
    # reads as 0, which is the same as "old enough to close".
    created_at: float = 0
    observation: OriginalCoverage | None = None


class WakeConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_event_id: str
    actor_id: str
    created_at: float
    expires_at: float
    prompt_commit_id: str | None = None
    prompt_event_id: str | None = None
    prompted_at: float | None = None


class SegmentSummaryRef(BaseModel):
    """One completed history batch selected into the final request."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    batch_id: str
    generation_version: str
    range: tuple[int, int, int, int]


class ConversationSegment(BaseModel):
    """Current source-window references, not a provider transcript or read grant."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str
    previous_id: str | None = None
    assembly_version: Literal[1] = 1
    opened_at: float
    reason: Literal['initial', 'process_restart', 'binding_changed', 'window_trimmed']
    model_profile: ModelProfile
    knowledge_revision: int = Field(ge=0)
    through_rowid: int = Field(ge=0)
    event_ids: list[str]
    summary_refs: list[SegmentSummaryRef] = Field(default_factory=list)
    result_aliases: dict[str, str] = Field(default_factory=dict)
    job_aliases: dict[str, str] = Field(default_factory=dict)


class SceneSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene_id: str
    version: int = 0
    knowledge_revision: int = 0
    conversation_segment: ConversationSegment | None = None
    last_observed_event_rowid: int = 0
    attention_scanned_event_rowid: int = 0
    pending_wakes: list[PendingWake] = Field(default_factory=list)
    focused_participants: dict[str, float] = Field(default_factory=dict)
    observing_until: float | None = None
    attention_sample_window: int = -1
    attention_sample_at: float | None = None
    attention_keyword_at: float | None = None
    attention_name_at: float | None = None
    participants: dict[str, ParticipantFacts] = Field(default_factory=dict)
    last_event_at: float = 0
    last_bot_message_at: float | None = None
    last_bot_message_event_id: str | None = None
    # Retired with the sampling draw; kept so sessions saved while the appetite
    # existed still load. Nothing reads them.
    engagement_level: float = 1.0
    engagement_at: float = 0
    consecutive_bot_messages: int = 0
    human_messages_since_bot: int = 0
    awake_until: float | None = None
    wake_source_event_id: str | None = None
    last_direct_human_at: float | None = None
    wake_confirmation: WakeConfirmationRequest | None = None
