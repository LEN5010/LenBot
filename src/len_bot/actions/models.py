from enum import StrEnum
from typing import Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
import uuid
from len_bot.media.models import MessageSegment, segment_text
from len_bot.events.models import EventType, PluginOrigin
from len_bot.cognition.models import AnswerBasis


class InterestPublication(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    interest_id: str
    revision: int = Field(ge=1)
    source_result_ids: list[str]
    resource_urls: list[str]

class DeliveryStatus(StrEnum):
    SENT = "sent"
    NOT_SENT = "not_sent"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class DeliveryResult(BaseModel):
    status: DeliveryStatus
    transport: str
    error_code: str | None = None
    error: str = ""
    message_id: str | None = None
    file_id: str | None = None
    file_receipt: dict | None = None


def receipt_delivery_status(event_type, payload, metadata) -> str | None:
    """Classify a stored terminal receipt, not an action or a send attempt."""
    if event_type not in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED,
                          EventType.FILE_UPLOADED, EventType.FILE_UPLOAD_FAILED, EventType.ACTION_SHADOWED}:
        return None
    if metadata.get('simulated') or payload.get('origin_mode') == 'simulated':
        return 'simulated'
    if event_type == EventType.ACTION_SHADOWED or payload.get('origin_mode') == 'shadow':
        return 'shadow'
    if payload.get('delivery_unknown') or payload.get('delivery_status') == 'unknown':
        return 'unknown'
    if event_type in {EventType.MESSAGE_SENT, EventType.FILE_UPLOADED}:
        platform_id = payload.get('file_id' if event_type == EventType.FILE_UPLOADED else 'message_id')
        identity_types = (str,) if event_type == EventType.FILE_UPLOADED else (str, int)
        if (isinstance(platform_id, bool) or not isinstance(platform_id, identity_types)
                or not str(platform_id).strip() or payload.get('delivery_status') not in {None, 'sent'}):
            return 'unknown'
        return 'sent'
    if payload.get('delivery_status') in {'not_sent', 'rejected'}:
        return payload['delivery_status']
    return 'unknown' if event_type == EventType.FILE_UPLOAD_FAILED or payload.get('delivery_status') == 'sent' else 'not_sent'

class ActionType(StrEnum):
    UPLOAD_GROUP_FILE = "UPLOAD_GROUP_FILE"
    SEND_GROUP_MESSAGE = "SEND_GROUP_MESSAGE"
    SEND_PRIVATE_MESSAGE = "SEND_PRIVATE_MESSAGE"


class AllMentionSegment(BaseModel):
    """Only Runtime's configured announcement path can create this segment."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["at_all"] = "at_all"

class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_asset_id: str | None = None
    resolved_file: str | None = Field(default=None, exclude=True)
    file_name: str | None = None
    interest_publication: InterestPublication | None = None
    source_started_at: float | None = None
    planned_at: float | None = None
    original_due_at: float | None = None
    delivery_late_seconds: float | None = None
    deferred_task_id: str | None = None
    wake_confirmation_request_id: str | None = None
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType
    scene_id: str
    segments: list[MessageSegment | AllMentionSegment] = Field(default_factory=list)
    output_kind: Literal['chat', 'plugin', 'command', 'announcement'] = 'chat'
    plugin_origin: PluginOrigin | None = None
    requester_qq_uid: str | None = None
    origin_event_id: str | None = None
    command_id: str | None = None
    announcement_member: str | None = None
    resolved_images: dict[str, str] = Field(default_factory=dict, exclude=True)
    resolved_sticker_ids: set[str] = Field(default_factory=set, exclude=True)
    batch_id: str | None = None
    episode_id: str | None = None
    checkpoint_index: int = Field(default=0,ge=0)
    batch_index: int = 0
    batch_size: int = 1
    reply_to: Optional[str] = None
    response_actor_ids: list[str] = Field(default_factory=list)
    release_focus_actor_ids: list[str] = Field(default_factory=list)
    associated_open_loop: Optional[dict[str, Any]] = None
    origin_mode: str = "live"
    acknowledges_task_id: str | None = None
    operation_ref: str | None = Field(default=None, min_length=1)
    fulfils_task_id: str | None = None
    job_id: str | None = None
    job_revision: int | None = None
    answer_basis: AnswerBasis | None = None

    @model_validator(mode="after")
    def operation_confirmation(self):
        if self.action_type == ActionType.UPLOAD_GROUP_FILE:
            if (not self.file_asset_id or self.segments or not self.job_id or self.job_revision is None
                    or not self.scene_id.startswith('group:') or self.reply_to or self.associated_open_loop
                    or self.operation_ref or self.acknowledges_task_id or self.interest_publication):
                raise ValueError('文件上传须有本群工作资产，不能混入消息、操作确认或互动关系')
        elif self.file_asset_id or not self.segments:
            raise ValueError('普通消息必须有片段，不能附带文件上传')
        if self.operation_ref and (self.acknowledges_task_id or self.fulfils_task_id
                                   or not self.batch_id or not self.origin_event_id or self.output_kind != 'chat'):
            raise ValueError("An operation confirmation needs its own committed turn and human source, without creation or fulfilment relations")
        if self.answer_basis and self.answer_basis.work_result:
            work = self.answer_basis.work_result
            if (work.job_id, work.revision) != (self.job_id, self.job_revision) or self.operation_ref or self.acknowledges_task_id:
                raise ValueError('行动的答复依据必须保留同一工作结果版本')
        return self

    @computed_field
    @property
    def content(self) -> str:
        if self.file_asset_id:
            return '[文件资产 ' + self.file_asset_id + ']'
        return ''.join('[全体成员]' if item.type == 'at_all' else segment_text([item]) for item in self.segments)
