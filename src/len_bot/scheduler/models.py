import time
from enum import StrEnum
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

class TaskStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    RESULT_READY = "result_ready"
    AWAITING_DELIVERY = "awaiting_delivery"
    COMPLETED = "completed"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    SHADOW_OBSERVED = "shadow_observed"
    REVIEW_REQUIRED = "review_required"
    CANCELLED = "cancelled"

class TaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    scene_id: str
    description: str
    due_at: float
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_id: str = "episode"
    created_at: float = Field(default_factory=time.time)
    # A condition-bound obligation fires when a committed event of this
    # type arrives in the task's scene, or at due_at deadline, whichever comes first.
    wake_event_type: Optional[str] = None
    wake_match: Optional[dict[str, Any]] = None
    origin_episode_id: Optional[str] = None
    origin_stimulus_id: Optional[str] = None
    trigger_event_id: Optional[str] = None
    origin_mode: str = "live"

    def __lt__(self, other: "TaskItem") -> bool:
        return self.due_at < other.due_at
