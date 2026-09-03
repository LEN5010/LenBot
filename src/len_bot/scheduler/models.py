import time
from enum import StrEnum
from typing import Any, Optional
from pydantic import BaseModel, Field

class TaskStatus(StrEnum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"

class TaskItem(BaseModel):
    id: str
    scene_id: str
    description: str
    due_at: float
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    source_event_id: str = "episode"
    created_at: float = Field(default_factory=time.time)
    # Condition-bound obligation (ADR-0018): fires when a committed event of this
    # type arrives in the task's scene, or at due_at deadline, whichever comes first.
    wake_event_type: Optional[str] = None

    def __lt__(self, other: "TaskItem") -> bool:
        return self.due_at < other.due_at
