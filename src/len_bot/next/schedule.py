"""One-time scene arrangements, their concrete permissions and model tools."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal, TYPE_CHECKING
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import LabConfig, ScheduleSettings

if TYPE_CHECKING:
    from .store import Schedule, Store


class ScheduleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    when: datetime
    note: str = Field(min_length=1)
    target: str = Field(alias="for", pattern=r"^(self|[1-9][0-9]*)$")
    requester: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")

    @field_validator("when", mode="before")
    @classmethod
    def absolute_time(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("when must be an ISO datetime with a UTC offset")
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("when must include a UTC offset; relative/recurring times are not supported")
        return parsed

    @field_validator("note")
    @classmethod
    def nonblank_note(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must not be blank")
        return value


class ScheduleListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["active", "all", "pending", "blocked", "delivered", "cancelled"] = "active"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=20)


class ScheduleCancelArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: int = Field(gt=0)
    requester: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")


SCHEDULE_TOOLS = [
    {"type": "function", "function": {
        "name": "schedule", "description": "为当前场景创建一次性安排。when 含 UTC 偏移且在未来；"
        "for=self 是未来自己要做的事，for=QQ 是提醒对象；requester 是实际请求人 QQ，Bot 自主安排用 null。"
        "相对时间请结合原话和当前时刻理解；返回已保存不等于提醒已发。",
        "parameters": ScheduleArguments.model_json_schema(),
    }},
    {"type": "function", "function": {
        "name": "schedule_list", "description": "查看当前场景的安排，含完整说明、实际请求人及交付状态；"
        "默认列未完成，分页按原定时间及安排 ID 排序，offset 为记录位置。",
        "parameters": ScheduleListArguments.model_json_schema(),
    }},
    {"type": "function", "function": {
        "name": "schedule_cancel", "description": "取消当前场景尚未交付的安排；requester 为实际操作者 QQ，"
        "本人可取消自己创建的，管理者可取消他人的；Bot 自主取消用 null，只能取消自主安排。",
        "parameters": ScheduleCancelArguments.model_json_schema(),
    }},
]


def identity_roles(settings: ScheduleSettings, requester: str, group_role: str | None) -> set[str]:
    roles = {"member"}
    if requester == settings.owner:
        roles.add("owner")
    if requester in settings.admins:
        roles.add("admin")
    if requester in settings.whitelist:
        roles.add("whitelist")
    if group_role in {"owner", "admin"}:
        roles.add("group_manager")
    return roles


def check_creation(settings: ScheduleSettings, *, requester: str | None, target: str,
                   bot_qq: str, group_role: str | None) -> None:
    if not settings.enabled:
        raise PermissionError("当前场景已关闭安排创建与到期执行")
    if requester is None:
        if target != "self":
            raise PermissionError("涉及人的提醒必须填写实际请求人 QQ")
        if not settings.autonomous:
            raise PermissionError("当前场景未开放 Bot 自主安排")
        return
    if requester == bot_qq:
        raise PermissionError("Bot 自主安排使用 requester=null，不作为人类请求人")
    roles = identity_roles(settings, requester, group_role)
    capability = "own" if target in {"self", requester} else "others"
    if not roles.intersection(getattr(settings, capability)):
        raise PermissionError(f"QQ {requester} 没有创建{'本人安排' if capability == 'own' else '他人提醒'}的权限")


def check_cancellation(settings: ScheduleSettings, *, requester: str | None,
                       creator: str | None, bot_qq: str, group_role: str | None) -> None:
    if requester == bot_qq:
        raise PermissionError("Bot 自主取消使用 requester=null，不作为人类操作者")
    if requester == creator:
        return
    if requester is None or not identity_roles(settings, requester, group_role).intersection(settings.manage):
        raise PermissionError("只能取消自己创建的安排，或由有管理能力的账号取消")


def platform_role(store: Store, config: LabConfig, requester: str | None) -> str | None:
    if requester is None or not config.scene.startswith("group:"):
        return None
    return store.latest_sender_role(config.scene, requester)


def display_time(at: float, timezone: str) -> str:
    return datetime.fromtimestamp(at, ZoneInfo(timezone)).isoformat()


def describe(item: Schedule, *, preview: bool = False) -> str:
    note = item.note
    if preview and len(note) > 160:
        note = note[:160] + "…（说明预览；用 schedule_list 查看全文）"
    creator = "Bot 自主" if item.requester is None else f"QQ {item.requester}"
    result = (f"#{item.id}；{item.status}；原定 {display_time(item.due_at, item.timezone)} ({item.timezone})；"
              f"创建者：{creator}；对象：{item.target}\n{note}")
    if item.delivered_at is not None:
        result += f"\n已交付会话：{display_time(item.delivered_at, item.timezone)}（不代表已发送提醒）"
    if item.reason is not None:
        result += "\n阻止原因：" + item.reason
    return result


def wake_text(item: Schedule, now: float) -> str:
    creator = "Bot 自主" if item.requester is None else f"QQ {item.requester}"
    return (f"[定时唤醒] #{item.id}；已交付当前会话，尚未发送提醒\n"
            f"创建者：{creator}；对象：{item.target}；创建于 {display_time(item.created, item.timezone)}\n"
            f"原定 {display_time(item.due_at, item.timezone)} ({item.timezone})；"
            f"实际唤醒 {display_time(now, item.timezone)}；延迟 {now - item.due_at:.3f} 秒\n{item.note}")


def execute_schedule(store: Store, config: LabConfig, name: Literal["schedule", "schedule_list", "schedule_cancel"],
                     arguments: dict, *, now: Callable[[], float] = time.time) -> str:
    if name == "schedule":
        args = ScheduleArguments.model_validate(arguments)
        when = args.when.timestamp()
        if when <= now():
            raise ValueError("when 必须晚于当前执行时刻；未创建过去的安排")
        check_creation(config.schedules, requester=args.requester, target=args.target,
                       bot_qq=config.bot_qq, group_role=platform_role(store, config, args.requester))
        item = store.create_schedule(config.scene, due_at=when, timezone=config.timezone,
                                     note=args.note, target=args.target, requester=args.requester,
                                     limit=config.schedules.max_pending)
        return "已保存安排；尚未唤醒或发送：\n" + describe(item)
    if name == "schedule_cancel":
        cancel = ScheduleCancelArguments.model_validate(arguments)
        item = store.get_schedule(config.scene, cancel.id)
        check_cancellation(config.schedules, requester=cancel.requester, creator=item.requester,
                           bot_qq=config.bot_qq, group_role=platform_role(store, config, cancel.requester))
        return "已取消安排：\n" + describe(store.cancel_schedule(config.scene, item.id))
    page = ScheduleListArguments.model_validate(arguments)
    items = store.list_schedules(config.scene, status=page.status, offset=page.offset, limit=page.limit + 1)
    more = len(items) > page.limit
    content = "\n\n".join(describe(item) for item in items[:page.limit]) or "没有符合条件的安排。"
    if more:
        content += f"\n\n尚有记录；下页 offset={page.offset + page.limit}。列表可能随新安排或状态变化。"
    return content
