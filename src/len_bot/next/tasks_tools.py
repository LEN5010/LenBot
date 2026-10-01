"""Native task tools: one external argument boundary and direct service dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .task_materials import MaterialSelection

if TYPE_CHECKING:
    from .tasks import WorkTasks


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
TaskAction = Literal["list", "status", "events", "read_event", "append", "continue", "answer", "cancel"]
TaskStatus = Literal["active", "all", "queued", "running", "waiting_input", "done", "failed", "cancelled"]


class DelegateArguments(BaseModel):
    model_config = STRICT

    goal: str
    deliverable: str
    requester: str = Field(pattern=r"^[1-9][0-9]*$")
    context: str = ""
    account_browser: bool = False
    materials: MaterialSelection = Field(default_factory=list, description='本场景共享普通资料的实际文件名选集；登记前复制为本任务私有只读快照，最多16份。不选择整个共享目录。')

    @field_validator("goal", "deliverable")
    @classmethod
    def nonblank_work(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal and deliverable must not be blank")
        return value


class TaskArguments(BaseModel):
    model_config = ConfigDict(**STRICT, json_schema_extra={
        "allOf": [{
            "if": {"properties": {"action": {"enum": ["append", "continue"]}}},
            "then": {"required": ["text"], "properties": {"text": {"type": "string", "pattern": r"\S"}}},
        }],
    })

    action: TaskAction
    id: int | None = Field(default=None, gt=0, strict=True)
    requester: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    text: str | None = Field(default=None,
        description="append/continue 必填非空补充原文；continue 沿用原任务会话与未被更正的要求。answer 的文字答复与 confirmed 二选一。")
    confirmed: bool | None = None
    question_id: str | None = Field(default=None, min_length=1)
    status: TaskStatus = "active"
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=20, ge=1, le=20, strict=True)
    snapshot: int | None = Field(default=None, ge=0, strict=True)
    event: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def action_fields(self) -> TaskArguments:
        allowed = {
            "list": {"action", "status", "offset", "limit"},
            "status": {"action", "id"},
            "events": {"action", "id", "requester", "offset", "snapshot"},
            "read_event": {"action", "id", "requester", "event", "offset"},
            "append": {"action", "id", "requester", "text"},
            "continue": {"action", "id", "requester", "text"},
            "answer": {"action", "id", "requester", "text", "confirmed", "question_id"},
            "cancel": {"action", "id", "requester"},
        }[self.action]
        unexpected = self.model_fields_set - allowed
        if unexpected:
            raise ValueError(f"task action {self.action!r} does not accept fields {sorted(unexpected)!r}")
        if self.action != "list" and self.id is None:
            raise ValueError(f"task action {self.action!r} requires id")
        if self.action in {"events", "read_event", "append", "continue", "answer", "cancel"} and self.requester is None:
            raise ValueError(f"task action {self.action!r} requires requester")
        if self.action == 'read_event' and self.event is None:
            raise ValueError('task read_event requires the actual event ID from events')
        if self.action == 'events' and self.offset > 0 and self.snapshot is None:
            raise ValueError('task events continuation requires its original snapshot')
        if self.action in {"append", "continue"} and (self.text is None or not self.text.strip()):
            raise ValueError(f"task action {self.action!r} requires nonblank text")
        if self.action == "answer":
            if self.question_id is None:
                raise ValueError("task answer requires the current question_id returned by task status")
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
    "不表示已开始执行、已满足交付物或已向平台发送文件。requester 必须是实际人类 QQ。"
    "account_browser=true仅在主人已明确同意此次专用账号浏览任务时使用；另建独立任务，不提升普通任务。",
    "parameters": DelegateArguments.model_json_schema(),
}}

TASK_TOOL = {"type": "function", "function": {
    "name": "task",
    "description": "查询或管理当前场景的真实任务：list/status 查看状态，append 追加运行中要求，"
    "events按真实requester权限读最近过程（每页5条、续页带snapshot），read_event按原event ID和字符offset读文本投影；"
    "continue 按请求人明确要求续接已结束任务；append/continue 必填非空 text。answer 回答待输入，cancel 取消。执行结束不等于目标完成；"
    "answer 的 question_id 使用当前 question.id，避免答复到已变化的另一个问题；"
    "文件已复制到交付区也不等于已上传到平台。需要操作者的动作填写实际 requester QQ。",
    "parameters": TaskArguments.model_json_schema(),
}}


async def execute_tasks(service: WorkTasks, scene: str, name: str, args: dict) -> dict:
    """Validate one native call, then let the owning service check permissions."""
    if name == "delegate":
        parsed = DelegateArguments.model_validate(args)
        return await service.delegate(
            scene, requester=parsed.requester, goal=parsed.goal,
            deliverable=parsed.deliverable, context=parsed.context, account_browser=parsed.account_browser,
            materials=parsed.materials,
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
    if parsed.action == 'events':
        return service.events(scene, parsed.id, requester=parsed.requester, offset=parsed.offset, snapshot=parsed.snapshot)
    if parsed.action == 'read_event':
        return service.read_event(scene, parsed.id, parsed.event, requester=parsed.requester, offset=parsed.offset)
    if parsed.action == "append":
        return await service.append(scene, parsed.id, requester=parsed.requester, text=parsed.text)
    if parsed.action == "continue":
        return await service.resume(scene, parsed.id, requester=parsed.requester, text=parsed.text)
    if parsed.action == "answer":
        return await service.answer(
            scene, parsed.id, requester=parsed.requester,
            text=parsed.text, confirmed=parsed.confirmed,
            question_id=parsed.question_id,
        )
    return await service.cancel(scene, parsed.id, requester=parsed.requester)
