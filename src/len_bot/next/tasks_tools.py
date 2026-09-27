"""Native task tools: one external argument boundary and direct service dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from .tasks import WorkTasks


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
TaskAction = Literal["list", "status", "append", "continue", "answer", "cancel"]
TaskStatus = Literal["active", "all", "queued", "running", "waiting_input", "done", "failed", "cancelled"]


class DelegateArguments(BaseModel):
    model_config = STRICT

    goal: str
    deliverable: str
    requester: str = Field(pattern=r"^[1-9][0-9]*$")
    context: str = ""

    @field_validator("goal", "deliverable")
    @classmethod
    def nonblank_work(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal and deliverable must not be blank")
        return value


class TaskArguments(BaseModel):
    model_config = STRICT

    action: TaskAction
    id: int | None = Field(default=None, gt=0, strict=True)
    requester: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    text: str | None = None
    confirmed: bool | None = None
    status: TaskStatus = "active"
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=20, ge=1, le=20, strict=True)

    @model_validator(mode="after")
    def action_fields(self) -> TaskArguments:
        allowed = {
            "list": {"action", "status", "offset", "limit"},
            "status": {"action", "id"},
            "append": {"action", "id", "requester", "text"},
            "continue": {"action", "id", "requester", "text"},
            "answer": {"action", "id", "requester", "text", "confirmed"},
            "cancel": {"action", "id", "requester"},
        }[self.action]
        unexpected = self.model_fields_set - allowed
        if unexpected:
            raise ValueError(f"task action {self.action!r} does not accept fields {sorted(unexpected)!r}")
        if self.action != "list" and self.id is None:
            raise ValueError(f"task action {self.action!r} requires id")
        if self.action in {"append", "continue", "answer", "cancel"} and self.requester is None:
            raise ValueError(f"task action {self.action!r} requires requester")
        if self.action in {"append", "continue"} and (self.text is None or not self.text.strip()):
            raise ValueError(f"task action {self.action!r} requires nonblank text")
        if self.action == "answer":
            text_supplied = "text" in self.model_fields_set
            confirmed_supplied = "confirmed" in self.model_fields_set
            if text_supplied == confirmed_supplied:
                raise ValueError("task answer requires exactly one of text or confirmed")
            if text_supplied and self.text is None:
                raise ValueError("task answer text must be a string; an empty string is allowed")
            if confirmed_supplied and self.confirmed is None:
                raise ValueError("task answer confirmed must be a boolean")
        return self


DELEGATE_TOOL = {"type": "function", "function": {
    "name": "delegate",
    "description": "为当前场景登记一项独立工作并排队。返回已创建只表示任务入队，"
    "不表示已开始执行、已满足交付物或已向平台发送文件。requester 必须是实际人类 QQ。",
    "parameters": DelegateArguments.model_json_schema(),
}}

TASK_TOOL = {"type": "function", "function": {
    "name": "task",
    "description": "查询或管理当前场景的真实任务：list/status 查看状态，append 追加运行中要求，"
    "continue 续接已结束任务，answer 回答待输入，cancel 取消。执行结束不等于目标完成；"
    "文件已复制到交付区也不等于已上传到平台。需要操作者的动作填写实际 requester QQ。",
    "parameters": TaskArguments.model_json_schema(),
}}


async def execute_tasks(service: WorkTasks, scene: str, name: str, args: dict) -> dict:
    """Validate one native call, then let the owning service check permissions."""
    if name == "delegate":
        parsed = DelegateArguments.model_validate(args)
        return await service.delegate(
            scene, requester=parsed.requester, goal=parsed.goal,
            deliverable=parsed.deliverable, context=parsed.context,
        )
    if name != "task":
        raise ValueError(f"unknown task tool {name!r}")
    parsed = TaskArguments.model_validate(args)
    return await perform_task_action(service, scene, parsed)


async def perform_task_action(service: WorkTasks, scene: str, parsed: TaskArguments) -> dict:
    if parsed.action == "list":
        return service.list(scene, status=parsed.status, offset=parsed.offset, limit=parsed.limit)
    if parsed.action == "status":
        return service.status(scene, parsed.id)
    if parsed.action == "append":
        return await service.append(scene, parsed.id, requester=parsed.requester, text=parsed.text)
    if parsed.action == "continue":
        return await service.resume(scene, parsed.id, requester=parsed.requester, text=parsed.text)
    if parsed.action == "answer":
        return await service.answer(
            scene, parsed.id, requester=parsed.requester,
            text=parsed.text, confirmed=parsed.confirmed,
        )
    return await service.cancel(scene, parsed.id, requester=parsed.requester)
