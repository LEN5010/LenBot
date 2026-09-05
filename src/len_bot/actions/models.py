from enum import StrEnum
from typing import Optional, Any
from pydantic import BaseModel, Field
import uuid

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

class ActionItem(BaseModel):
    source_started_at: float | None = None
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_type: ActionType
    scene_id: str
    content: str
    reply_to: Optional[str] = None
    associated_open_loop: Optional[dict[str, Any]] = None
    origin_mode: str = "live"
    fulfils_task_id: str | None = None
