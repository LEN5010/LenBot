"""Event-derived session facts. Social interpretations belong to a single turn."""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Any, Literal
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
    range: list[int] = Field(min_length=4, max_length=4)


class SegmentPageReply(BaseModel):
    """A native tool reply shown from a saved observation; the body stays there.

    offset/limit/unit are the presentation inputs that produced the page, and
    displayed_range is what they displayed: [start, end, total] of the saved
    body, the original message's own range for read_message_range, and None
    for a job directory page, which displays no range of its JSON.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['page', 'message_range', 'job_query']
    tool_call_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    presentation: str = Field(min_length=1)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    coordinate_unit: Literal['characters', 'records']
    displayed_range: list[int] | None = Field(default=None, min_length=3, max_length=3)
    shown: Literal['page', 'archived'] = 'page'


class SegmentReceiptReply(BaseModel):
    """Host-built minimal receipt for a reply that has no saved observation.

    Only these fixed fields are kept; the text the model received, including
    any hook output, is not, and is never rebuilt from traces or commits.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['receipt'] = 'receipt'
    tool_call_id: str = Field(min_length=1)
    handle: str | None = None
    committed: bool
    reason: str | None = None


SegmentReply = Annotated[SegmentPageReply | SegmentReceiptReply, Field(discriminator='kind')]


class SegmentExchange(BaseModel):
    """One complete native call group, placed after a scene event position."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['exchange'] = 'exchange'
    episode_id: str = Field(min_length=1)
    after_rowid: int = Field(ge=0)
    continuation: dict[str, Any]
    replies: list[SegmentReply] = Field(min_length=1)

    @model_validator(mode='after')
    def matching_replies(self):
        calls = self.continuation.get('tool_calls')
        if self.continuation.get('role') != 'assistant' or not isinstance(calls, list) or not calls:
            raise ValueError('A saved exchange needs a native assistant tool-call continuation')
        if any(key.startswith('_') for key in self.continuation):
            raise ValueError('A saved continuation carries no host-private fields')
        ids = [call.get('id') if isinstance(call, dict) else None for call in calls]
        if ids != [reply.tool_call_id for reply in self.replies]:
            raise ValueError('Each native tool call needs exactly one reply, in call order')
        return self

    @property
    def call_ids(self) -> list[str]:
        return [reply.tool_call_id for reply in self.replies]

    def same_source(self, other: 'SegmentExchange') -> bool:
        """The same group; a page may since have been archived, never restored."""
        if (self.episode_id, self.after_rowid, self.continuation) != (other.episode_id, other.after_rowid,
                                                                     other.continuation):
            return False
        if len(self.replies) != len(other.replies):
            return False
        for earlier, later in zip(self.replies, other.replies):
            if earlier.kind == 'receipt' or later.kind == 'receipt':
                if earlier != later:
                    return False
            elif (earlier.model_dump(exclude={'shown'}) != later.model_dump(exclude={'shown'})
                  or earlier.shown == 'archived' and later.shown != 'archived'):
                return False
        return True


class SegmentExchangeGap(BaseModel):
    """A run whose native groups could not be kept; the next run opens a new segment."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    episode_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ConversationSegment(BaseModel):
    """Current source-window references and complete native exchanges, not a read grant."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str
    previous_id: str | None = None
    assembly_version: Literal[1] = 1
    opened_at: float
    reason: Literal['initial', 'process_restart', 'binding_changed', 'window_trimmed', 'exchange_unrecoverable']
    model_profile: ModelProfile
    knowledge_revision: int = Field(ge=0)
    through_rowid: int = Field(ge=0)
    event_ids: list[str]
    summary_refs: list[SegmentSummaryRef] = Field(default_factory=list)
    result_aliases: dict[str, str] = Field(default_factory=dict)
    job_aliases: dict[str, str] = Field(default_factory=dict)
    memory_aliases: dict[str, str] = Field(default_factory=dict)
    task_aliases: dict[str, str] = Field(default_factory=dict)
    loop_aliases: dict[str, str] = Field(default_factory=dict)
    ordered_items: list[SegmentExchange] = Field(default_factory=list)
    exchange_gap: SegmentExchangeGap | None = None


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
