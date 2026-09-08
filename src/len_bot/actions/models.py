from enum import StrEnum
from typing import Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, computed_field
import uuid
from len_bot.media.models import MessageSegment, segment_text

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

class ActionType(StrEnum):
    SEND_GROUP_MESSAGE = "SEND_GROUP_MESSAGE"
    SEND_PRIVATE_MESSAGE = "SEND_PRIVATE_MESSAGE"


class AllMentionSegment(BaseModel):
    """Only Runtime's configured announcement path can create this segment."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["at_all"] = "at_all"

class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_started_at: float | None = None
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType
    scene_id: str
    segments: list[MessageSegment | AllMentionSegment] = Field(min_length=1)
    output_kind: Literal['chat', 'command', 'announcement'] = 'chat'
    requester_qq_uid: str | None = None
    origin_event_id: str | None = None
    command_id: str | None = None
    announcement_member: str | None = None
    resolved_images: dict[str, str] = Field(default_factory=dict, exclude=True)
    resolved_sticker_ids: set[str] = Field(default_factory=set, exclude=True)
    batch_id: str | None = None
    batch_index: int = 0
    batch_size: int = 1
    reply_to: Optional[str] = None
    response_actor_ids: list[str] = Field(default_factory=list)
    associated_open_loop: Optional[dict[str, Any]] = None
    origin_mode: str = "live"
    acknowledges_task_id: str | None = None
    fulfils_task_id: str | None = None
    job_id: str | None = None
    job_revision: int | None = None

    @computed_field
    @property
    def content(self) -> str:
        return ''.join('[全体成员]' if item.type == 'at_all' else segment_text([item]) for item in self.segments)
