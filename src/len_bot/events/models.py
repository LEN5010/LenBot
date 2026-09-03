from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field
import uuid
import time

class EventType(StrEnum):
    GROUP_MESSAGE_RECEIVED = "GROUP_MESSAGE_RECEIVED"
    PRIVATE_MESSAGE_RECEIVED = "PRIVATE_MESSAGE_RECEIVED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_SEND_FAILED = "MESSAGE_SEND_FAILED"
    TASK_DUE = "TASK_DUE"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    USER_JOINED = "USER_JOINED"
    HISTORICAL_IMPORT = "HISTORICAL_IMPORT"

class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    scene_id: str
    actor_id: str
    timestamp: float = Field(default_factory=time.time)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def raw_text(self) -> str:
        return self.payload.get("raw_text", "")

    @property
    def is_mention_bot(self) -> bool:
        return bool(self.payload.get("at_bot", False))

    @property
    def is_reply_bot(self) -> bool:
        return bool(self.payload.get("reply_bot", False))

class StimulusType(StrEnum):
    SINGLE_MESSAGE = "SINGLE_MESSAGE"
    SOCIAL_MESSAGE_BURST = "SOCIAL_MESSAGE_BURST"
    PROACTIVE_TASK = "PROACTIVE_TASK"

class Stimulus(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scene_id: str
    stimulus_type: StimulusType
    source_event_ids: list[str]
    actor_id: str
    combined_text: str
    has_mention_bot: bool = False
    has_reply_bot: bool = False
    timestamp: float = Field(default_factory=time.time)
