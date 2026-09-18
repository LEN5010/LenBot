"""Event-derived session facts. Social interpretations belong to a single turn."""
from pydantic import BaseModel, ConfigDict, Field


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


class WakeConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_event_id: str
    actor_id: str
    created_at: float
    expires_at: float
    prompt_commit_id: str | None = None
    prompt_event_id: str | None = None
    prompted_at: float | None = None


class SceneSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene_id: str
    version: int = 0
    knowledge_revision: int = 0
    last_observed_event_rowid: int = 0
    attention_scanned_event_rowid: int = 0
    pending_wakes: list[PendingWake] = Field(default_factory=list)
    focused_participants: dict[str, float] = Field(default_factory=dict)
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
