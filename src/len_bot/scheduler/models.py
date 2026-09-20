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


class ReminderControlSnapshot(BaseModel):
    """The existing reminder facts an editor actually selected, not a new version."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str
    scene_id: str
    description: str
    due_at: float = Field(allow_inf_nan=False)
    status: TaskStatus
    created_at: float = Field(allow_inf_nan=False)
    source_event_id: str
    wake_event_type: str | None
    wake_match: dict[str, Any] | None
    trigger_event_id: str | None

    @classmethod
    def from_task(cls, task: dict):
        return cls.model_validate({name: task[name] for name in cls.model_fields})


def task_delivery_available(status: TaskStatus | str, payload: dict[str, Any]) -> bool:
    """Mirror the existing delivery claim; seeing a task never makes it ready."""
    return (status in {TaskStatus.PROCESSING, TaskStatus.RESULT_READY}
            and payload.get('delivery_action_id') is None
            and (payload.get('kind') != 'query' or payload.get('result') is not None))


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
