"""Task panel response shapes, sharing the persisted task record type."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, JsonValue

from .tasks_store import Task


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskView(Task):
    workspace_discard_requested: bool
    active_timeout_seconds: float | None


class RegisteredFile(BaseModel):
    id: int
    task_id: int
    name: str
    size: int
    note: str | None
    status: Literal['registered']
    upload: dict[str, JsonValue] | None


class TaskEventPreview(BaseModel):
    id: int
    kind: str
    created: float
    delivered_at: float | None
    event_type: str | None
    tool_name: str | None
    browser_method: str | None
    preview: str
    truncated: bool


class TaskPage(BaseModel):
    items: list[Task]
    next_offset: int | None


class TaskDetail(BaseModel):
    task: TaskView
    events: list[TaskEventPreview]
    network: dict[str, JsonValue] | None
    next_after: int
    files: list[RegisteredFile]
