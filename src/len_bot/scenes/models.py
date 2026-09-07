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
    attention_keyword_at: float | None = None
    participants: dict[str, ParticipantFacts] = Field(default_factory=dict)
    last_event_at: float = 0
    last_bot_message_at: float | None = None
    last_bot_message_event_id: str | None = None
    consecutive_bot_messages: int = 0
    human_messages_since_bot: int = 0
