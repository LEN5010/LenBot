from enum import StrEnum
from typing import Annotated, Any, Literal
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

    def depends_on(self, plugin_id: str) -> bool:
        return self.plugin_id==plugin_id or bool(self.handler_origin and self.handler_origin.depends_on(plugin_id))


class PluginEventPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    plugin_id: str
    plugin_version: str
    name: str
    data: dict[str, Any]


class HumanInitiator(BaseModel):
    """A real person's own words are the only source of a human initiator."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    principal_type: Literal['human'] = 'human'
    user_id: str = Field(min_length=1, description="真实 QQ UID，不是显示名或自报身份")
    request_event_id: str = Field(min_length=1)

    @property
    def billing_subject(self) -> str:
        return 'user:' + self.user_id


class SystemInitiator(BaseModel):
    """Runtime, Scheduler and panel operators; never a fabricated QQ account."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    principal_type: Literal['system'] = 'system'
    agent_id: str = Field(min_length=1, description="runtime / scheduler / operator:<账号>")
    trigger_event_id: str = Field(min_length=1, description="真实系统事件 ID，由 Runtime 或 Scheduler 生成")
    purpose: str | None = Field(default=None, min_length=1, description="明确用途；计费与授权主体用它区分")
    cycle_id: str | None = None

    @property
    def billing_subject(self) -> str:
        return 'system:' + (self.purpose or self.agent_id)


class PluginInitiator(BaseModel):
    """A plugin's own entry; its human request source stays a separate fact."""
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    principal_type: Literal['plugin'] = 'plugin'
    plugin_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)

    @property
    def billing_subject(self) -> str:
        return 'system:plugin:' + self.plugin_id


Initiator = Annotated[HumanInitiator | SystemInitiator | PluginInitiator,
                      Field(discriminator='principal_type')]


def legacy_initiator(requester_qq_uid: str | None, request_source_event_id: str | None) -> Initiator | None:
    """Convert an existing work record from its own exact fields.

    Older work that lacks a definite request anchor keeps no initiator; it is
    never inferred from accumulated evidence.
    """
    if requester_qq_uid and request_source_event_id:
        return HumanInitiator(user_id=str(requester_qq_uid), request_event_id=request_source_event_id)
    return None


def human_event_uid(event) -> str | None:
    """The real QQ UID behind a human message event, or None.

    This is the one place that decides whether an event may stand for a human
    request.  Callers branch on the result instead of comparing a possibly
    empty UID, so a system or plugin source can never be read as a person.
    """
    if event is None or event.event_type not in (EventType.GROUP_MESSAGE_RECEIVED,
                                                 EventType.PRIVATE_MESSAGE_RECEIVED):
        return None
    actor_id = event.actor_id or ''
    if not actor_id.startswith('user:'):
        return None
    uid = actor_id.removeprefix('user:')
    return uid if uid.isdigit() and uid[0] != '0' else None


def human_initiator_for(event, bot_actor_id: str = '') -> HumanInitiator | None:
    """Build the human branch only from a real person's own stored message."""
    if bot_actor_id and event is not None and event.actor_id == bot_actor_id:
        return None
    uid = human_event_uid(event)
    return HumanInitiator(user_id=uid, request_event_id=event.id) if uid else None


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
