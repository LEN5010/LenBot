from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
import uuid
import time

class EventType(StrEnum):
    GROUP_MESSAGE_RECEIVED = "GROUP_MESSAGE_RECEIVED"
    PRIVATE_MESSAGE_RECEIVED = "PRIVATE_MESSAGE_RECEIVED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_SEND_FAILED = "MESSAGE_SEND_FAILED"
    ACTION_SHADOWED = "ACTION_SHADOWED"
    TASK_DUE = "TASK_DUE"
    TASK_REVIEW = "TASK_REVIEW"
    REFLECTION_RECORDED = "REFLECTION_RECORDED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_OBSERVATION_RECORDED = "TOOL_OBSERVATION_RECORDED"
    AGENT_JOB_CONTROL = "AGENT_JOB_CONTROL"
    AGENT_JOB_CHECKPOINT = "AGENT_JOB_CHECKPOINT"
    AGENT_JOB_FINISHED = "AGENT_JOB_FINISHED"
    AGENT_JOB_PROGRESS = "AGENT_JOB_PROGRESS"
    OPERATOR_ACTION = "OPERATOR_ACTION"
    CONVERSATION_COMMITTED = "CONVERSATION_COMMITTED"
    # Retired producers; immutable audit history still uses these event types.
    ROLLOUT_UPDATED = "ROLLOUT_UPDATED"
    REPLY_FEEDBACK_LABELLED = "REPLY_FEEDBACK_LABELLED"
    MEDIA_UPDATED = "MEDIA_UPDATED"
    USER_JOINED = "USER_JOINED"
    HISTORICAL_IMPORT = "HISTORICAL_IMPORT"
    STATE_ANNOTATION = "STATE_ANNOTATION"
    LIVE_STARTED = "LIVE_STARTED"
    LIVE_ENDED = "LIVE_ENDED"
    PLUGIN_EVENT = "PLUGIN_EVENT"
    SOCIAL_COGNITION_RECORDED = "SOCIAL_COGNITION_RECORDED"

class PluginOrigin(BaseModel):
    """Stored ownership of a real plugin invocation, including its parent."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    plugin_id: str
    plugin_version: str
    entry_id: str
    entry_kind: Literal['handler', 'tool']
    run_id: str
    source_event_id: str
    parent_run_id: str | None = None
    parent_tool_call_id: str | None = None
    scene_entry: Literal['chat', 'handler', 'work'] = 'chat'
    handler_origin: 'PluginOrigin | None' = None


class PluginEventPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    plugin_id: str
    plugin_version: str
    name: str
    data: dict[str, Any]


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
        return self.payload.get("raw_text") or self.payload.get("content", "")

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
    PLUGIN_FACT = "PLUGIN_FACT"

class Stimulus(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scene_id: str
    stimulus_type: StimulusType
    source_event_ids: list[str]
    actor_id: str
    combined_text: str
    has_mention_bot: bool = False
    has_reply_bot: bool = False
    origin_mode: str = "live"
    timestamp: float = Field(default_factory=time.time)
    events: list[Event] = Field(default_factory=list)
