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


class SceneSession(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene_id: str
    version: int = 0
    knowledge_revision: int = 0
    last_observed_event_rowid: int = 0
    last_cognized_event_rowid: int = 0
    participants: dict[str, ParticipantFacts] = Field(default_factory=dict)
    last_event_at: float = 0
    last_bot_message_at: float | None = None
    last_bot_message_event_id: str | None = None
    consecutive_bot_messages: int = 0
    human_messages_since_bot: int = 0
