"""Known archived event/session data for offline rollback; no old runtime or model clients."""

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field

class ArchivedEventType(StrEnum):
    GROUP_MESSAGE_RECEIVED = 'GROUP_MESSAGE_RECEIVED'
    PRIVATE_MESSAGE_RECEIVED = 'PRIVATE_MESSAGE_RECEIVED'
    FILE_UPLOADED = 'FILE_UPLOADED'
    FILE_UPLOAD_FAILED = 'FILE_UPLOAD_FAILED'
    MESSAGE_SENT = 'MESSAGE_SENT'
    DELIVERY_ATTEMPTED = 'DELIVERY_ATTEMPTED'
    SCENE_WAKE_CONFIRMED = 'SCENE_WAKE_CONFIRMED'
    PLATFORM_ACTION_ATTEMPTED = 'PLATFORM_ACTION_ATTEMPTED'
    PLATFORM_ACTION_RESULT = 'PLATFORM_ACTION_RESULT'
    CORE_BRIDGE_ATTEMPTED = 'CORE_BRIDGE_ATTEMPTED'
    ACTION_REQUESTED = 'ACTION_REQUESTED'
    ACTION_REVIEW_STARTED = 'ACTION_REVIEW_STARTED'
    PUBLIC_INTEREST_CHANGED = 'PUBLIC_INTEREST_CHANGED'
    MESSAGE_SEND_FAILED = 'MESSAGE_SEND_FAILED'
    ACTION_SHADOWED = 'ACTION_SHADOWED'
    TASK_DUE = 'TASK_DUE'
    TASK_REVIEW = 'TASK_REVIEW'
    REFLECTION_RECORDED = 'REFLECTION_RECORDED'
    TOOL_COMPLETED = 'TOOL_COMPLETED'
    TOOL_OBSERVATION_RECORDED = 'TOOL_OBSERVATION_RECORDED'
    AGENT_JOB_CONTROL = 'AGENT_JOB_CONTROL'
    AGENT_JOB_CHECKPOINT = 'AGENT_JOB_CHECKPOINT'
    AGENT_JOB_FINISHED = 'AGENT_JOB_FINISHED'
    AGENT_JOB_PROGRESS = 'AGENT_JOB_PROGRESS'
    OPERATOR_ACTION = 'OPERATOR_ACTION'
    CONVERSATION_COMMITTED = 'CONVERSATION_COMMITTED'
    ROLLOUT_UPDATED = 'ROLLOUT_UPDATED'
    REPLY_FEEDBACK_LABELLED = 'REPLY_FEEDBACK_LABELLED'
    MEDIA_UPDATED = 'MEDIA_UPDATED'
    USER_JOINED = 'USER_JOINED'
    HISTORICAL_IMPORT = 'HISTORICAL_IMPORT'
    STATE_ANNOTATION = 'STATE_ANNOTATION'
    LIVE_STARTED = 'LIVE_STARTED'
    LIVE_ENDED = 'LIVE_ENDED'
    PLUGIN_EVENT = 'PLUGIN_EVENT'
    SOCIAL_COGNITION_RECORDED = 'SOCIAL_COGNITION_RECORDED'


class ArchivedEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    event_type: ArchivedEventType
    scene_id: str
    actor_id: str
    timestamp: float = Field(allow_inf_nan=False)
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def raw_text(self) -> str:
        # All exported message projections explicitly carry their original text.
        return self.payload['raw_text']


class ArchivedParticipant(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actor_id: str
    nickname: str | None = None
    card: str | None = None
    role: str | None = None


class ArchivedSender(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    nickname: str | None = None
    card: str | None = None
    role: str | None = None


class ArchivedSession(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scene_id: str
    version: int
    knowledge_revision: int
    conversation_segment: dict | None
    last_observed_event_rowid: int
    attention_scanned_event_rowid: int
    pending_wakes: list[dict]
    focused_participants: dict[str, float]
    observing_until: float | None
    attention_sample_window: int
    attention_sample_at: float | None
    attention_keyword_at: float | None
    attention_name_at: float | None
    participants: dict[str, ArchivedParticipant]
    last_event_at: float
    last_bot_message_at: float | None
    last_bot_message_event_id: str | None
    engagement_level: float
    engagement_at: float
    consecutive_bot_messages: int
    human_messages_since_bot: int
    awake_until: float | None
    wake_source_event_id: str | None
    last_direct_human_at: float | None
    wake_confirmation: dict | None


def new_session(scene: str) -> ArchivedSession:
    """Initialize the known old format, not a reconstructed cognitive episode."""
    return ArchivedSession(
        scene_id=scene,
        version=0,
        knowledge_revision=0,
        conversation_segment=None,
        last_observed_event_rowid=0,
        attention_scanned_event_rowid=0,
        pending_wakes=[],
        focused_participants={},
        observing_until=None,
        attention_sample_window=-1,
        attention_sample_at=None,
        attention_keyword_at=None,
        attention_name_at=None,
        participants={},
        last_event_at=0,
        last_bot_message_at=None,
        last_bot_message_event_id=None,
        engagement_level=1.0,
        engagement_at=0,
        consecutive_bot_messages=0,
        human_messages_since_bot=0,
        awake_until=None,
        wake_source_event_id=None,
        last_direct_human_at=None,
        wake_confirmation=None,
    )


def real_send(event: ArchivedEvent, bot_actor_id: str) -> bool:
    if (event.event_type != ArchivedEventType.MESSAGE_SENT or event.actor_id != bot_actor_id
            or event.payload.get('origin_mode') != 'live' or event.metadata.get('simulated')
            or event.payload.get('delivery_unknown') or event.payload.get('delivery_status') == 'unknown'):
        return False
    identity = event.payload.get('message_id')
    return (not isinstance(identity, bool) and isinstance(identity, (str, int))
            and bool(str(identity).strip()) and event.payload.get('delivery_status') in {None, 'sent'})


def project_message_facts(state: ArchivedSession, event: ArchivedEvent, bot_actor_id: str) -> ArchivedSession:
    result = state.model_copy(deep=True)
    result.version += 1
    result.last_event_at = event.timestamp
    if event.event_type in {ArchivedEventType.GROUP_MESSAGE_RECEIVED, ArchivedEventType.PRIVATE_MESSAGE_RECEIVED}:
        if event.actor_id != bot_actor_id:
            result.consecutive_bot_messages = 0
            result.human_messages_since_bot += 1
            person = result.participants.get(event.actor_id)
            if person is None:
                person = ArchivedParticipant(actor_id=event.actor_id)
            sender = event.payload.get('sender')
            if sender is not None:
                # Parse supplied facts once; omitted sender fields do not erase prior facts.
                supplied = ArchivedSender.model_validate(sender)
                for field in ('nickname', 'card', 'role'):
                    if field in sender:
                        setattr(person, field, getattr(supplied, field))
            result.participants[event.actor_id] = person
    elif (not event.metadata.get('conversation_excluded') and real_send(event, bot_actor_id)):
        result.last_bot_message_at = event.timestamp
        result.last_bot_message_event_id = event.id
        result.consecutive_bot_messages += 1
        result.human_messages_since_bot = 0
    return result
