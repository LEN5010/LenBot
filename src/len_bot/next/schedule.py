"""Scene arrangements, their concrete permissions and model tools."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
import re
from typing import Literal, TYPE_CHECKING
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .identity import roles_for

from .config import LabConfig, ScheduleSettings
from .schedule_time import Cron, next_cron, parse_cron

if TYPE_CHECKING:
    from .store import Schedule, Store


_INTERVAL = re.compile(r"every ([1-9][0-9]*)([mhd])\Z")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_MAX_INTERVAL_SECONDS = 365 * 86400


class ScheduleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    when: datetime | int | Cron
    note: str = Field(min_length=1)
    target: str = Field(alias="for", pattern=r"^(self|[1-9][0-9]*)$")
    requester: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")

    @field_validator("when", mode="before", json_schema_input_type=str)
    @classmethod
    def supported_time(cls, value: object) -> datetime | int | Cron:
        if not isinstance(value, str):
            raise ValueError("when must be an offset ISO datetime, 'every <positive integer>m|h|d', or cron")
        if value.startswith("cron:"):
            return parse_cron(value)
        interval = _INTERVAL.fullmatch(value)
        if interval is not None:
            number, unit = interval.groups()
            seconds = int(number) * _UNIT_SECONDS[unit]
            if seconds > _MAX_INTERVAL_SECONDS:
                raise ValueError("interval must be at most 365 days")
            return seconds
        if value.startswith("every "):
            raise ValueError("fixed interval supports only 'every <positive integer>m|h|d'")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"when must be an ISO datetime with a UTC offset or fixed interval: {error}") from error
        if parsed.utcoffset() is None:
            raise ValueError("when must include a UTC offset; local/relative times are not supported")
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
        "name": "schedule", "description": "为当前场景创建一次性或周期安排。when 为未来且含 UTC 偏移的 ISO 时间，"
        "或严格 every <正整数>m|h|d（1分钟至365天固定UTC秒，d=24小时），"
        "或 cron:<分钟> <小时> <日> <月> <星期>，按场景时区；每字段支持 *、数字、a-b、逗号列表、*/n 与 a-b/n，"
        "星期 0-6（0 为周日），日与星期不能同时限制，不支持英文名、L、W、#。"
        "缺失/重复时刻明确报错或阻止后续，不自动顺延、选偏移。"
        "for=self 是未来自己要做的事，for=QQ 是提醒对象；requester 是实际请求人 QQ，Bot 自主安排用 null。"
        "相对时间请结合原话和当前时刻理解；返回已保存不等于提醒已发。",
        "parameters": ScheduleArguments.model_json_schema(),
    }},
    {"type": "function", "function": {
        "name": "schedule_list", "description": "查看当前场景的安排，含完整说明、实际请求人及交付状态；"
        "默认列未完成，分页按当前待办时间及安排 ID 排序，offset 为记录位置。",
        "parameters": ScheduleListArguments.model_json_schema(),
    }},
    {"type": "function", "function": {
        "name": "schedule_cancel", "description": "取消当前场景未完成的一次性或周期安排；requester 为实际操作者 QQ，"
        "本人可取消自己创建的，管理者可取消他人的；Bot 自主取消用 null，只能取消自主安排。"
        "取消周期安排停止后续唤醒，不撤回已交给会话的过去次数。",
        "parameters": ScheduleCancelArguments.model_json_schema(),
    }},
]


def effective_settings(config: LabConfig) -> ScheduleSettings:
    return config.schedules.model_copy(update={
        'admins': list(dict.fromkeys([*config.permissions.admins, *config.schedules.admins])),
        'whitelist': list(dict.fromkeys([*config.permissions.whitelist, *config.schedules.whitelist]))})


def identity_roles(settings: ScheduleSettings, requester: str, group_role: str | None,
                   root_owner: str | None = None) -> set[str]:
    return roles_for(requester, owner=root_owner, scoped_owner=settings.owner,
                     admins=settings.admins, whitelist=settings.whitelist, group_role=group_role)


def check_creation(settings: ScheduleSettings, *, requester: str | None, target: str,
                   bot_qq: str, group_role: str | None, root_owner: str | None = None) -> None:
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
    roles = identity_roles(settings, requester, group_role, root_owner)
    capability = "own" if target in {"self", requester} else "others"
    if not roles.intersection(getattr(settings, capability)):
        raise PermissionError(f"QQ {requester} 没有创建{'本人安排' if capability == 'own' else '他人提醒'}的权限")


def check_cancellation(settings: ScheduleSettings, *, requester: str | None,
                       creator: str | None, bot_qq: str, group_role: str | None, root_owner: str | None = None) -> None:
    if requester == bot_qq:
        raise PermissionError("Bot 自主取消使用 requester=null，不作为人类操作者")
    if requester == creator:
        return
    if requester is None or not identity_roles(settings, requester, group_role, root_owner).intersection(settings.manage):
        raise PermissionError("只能取消自己创建的安排，或由有管理能力的账号取消")


def platform_role(store: Store, config: LabConfig, requester: str | None) -> str | None:
    if requester is None or not config.scene.startswith("group:"):
        return None
    return store.latest_sender_role(config.scene, requester)


def display_time(at: float, timezone: str) -> str:
    return datetime.fromtimestamp(at, ZoneInfo(timezone)).isoformat()


def _interval_label(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400}天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}小时"
    return f"{seconds // 60}分钟"


def describe(item: Schedule, *, preview: bool = False) -> str:
    note = item.note
    if preview and len(note) > 160:
        note = note[:160] + "…（说明预览；用 schedule_list 查看全文）"
    creator = "Bot 自主" if item.requester is None else f"QQ {item.requester}"
    if item.interval_seconds is None and item.cron is None:
        timing = f"原定 {display_time(item.due_at, item.timezone)} ({item.timezone})"
    else:
        label = "下次" if item.status == "pending" else "已保存原定" if item.status == "blocked" else "取消前原定"
        recurrence = (f"每隔 {_interval_label(item.interval_seconds)}（固定UTC秒）"
                      if item.interval_seconds is not None else
                      f"按 {item.cron}（{item.timezone}）")
        timing = (f"{recurrence}；"
                  f"{label} {display_time(item.due_at, item.timezone)} ({item.timezone})")
    result = (f"#{item.id}；{item.status}；{timing}；"
              f"创建者：{creator}；对象：{item.target}\n{note}")
    if item.delivered_at is not None:
        prior = "上次已交付会话" if item.interval_seconds is not None or item.cron is not None else "已交付会话"
        result += f"\n{prior}：{display_time(item.delivered_at, item.timezone)}（不代表已发送提醒）"
    if item.reason is not None:
        result += "\n阻止原因：" + item.reason
    return result


def wake_text(item: Schedule, now: float) -> str:
    creator = "Bot 自主" if item.requester is None else f"QQ {item.requester}"
    timing = ""
    recurring = item.interval_seconds is not None or item.cron is not None
    if recurring:
        recurrence = (f"每隔 {_interval_label(item.interval_seconds)}（固定UTC秒）"
                      if item.interval_seconds is not None else
                      f"按 {item.cron}（{item.timezone}）")
        timing = (f"周期{recurrence}；"
                  "离线错过多次也只补交一次；本次交付后用 schedule_list 查看持久化的下一时刻。\n")
        if item.delivered_at is not None:
            timing += (f"上次已交付会话：{display_time(item.delivered_at, item.timezone)}"
                       "（不代表已发送提醒）。\n")
    due_label = "本期原定" if recurring else "原定"
    return (f"[定时唤醒] #{item.id}；已交付当前会话，尚未发送提醒\n"
            f"创建者：{creator}；对象：{item.target}；创建于 {display_time(item.created, item.timezone)}\n"
            f"{timing}{due_label} {display_time(item.due_at, item.timezone)} ({item.timezone})；"
            f"实际唤醒 {display_time(now, item.timezone)}；延迟 {now - item.due_at:.3f} 秒\n{item.note}")


def create_arrangement(store: Store, config: LabConfig, args: ScheduleArguments, *,
                       now: Callable[[], float] = time.time) -> Schedule:
    started = now()
    interval_seconds = args.when if isinstance(args.when, int) else None
    cron = args.when if isinstance(args.when, Cron) else None
    if interval_seconds is not None:
        when = started + interval_seconds
    elif cron is not None:
        when = next_cron(cron, config.timezone, started)
    else:
        when = args.when.timestamp()
    if when <= started:
        raise ValueError("when 必须晚于当前执行时刻；未创建过去的安排")
    if args.requester in config.permissions.blacklist:
        raise PermissionError('当前黑名单账号不能创建安排')
    check_creation(effective_settings(config), requester=args.requester, target=args.target,
                   bot_qq=config.bot_qq, root_owner=config.owner_qq, group_role=platform_role(store, config, args.requester))
    return store.create_schedule(config.scene, due_at=when, timezone=config.timezone,
                                 note=args.note, target=args.target, requester=args.requester,
                                 limit=config.schedules.max_pending, interval_seconds=interval_seconds,
                                 cron=None if cron is None else cron.expression)


def cancel_arrangement(store: Store, config: LabConfig, *, id: int,
                       requester: str | None) -> Schedule:
    item = store.get_schedule(config.scene, id)
    if requester != item.requester and requester in config.permissions.blacklist:
        raise PermissionError('黑名单账号只能取消本人的安排')
    check_cancellation(effective_settings(config), requester=requester, creator=item.requester,
                       bot_qq=config.bot_qq, root_owner=config.owner_qq, group_role=platform_role(store, config, requester))
    return store.cancel_schedule(config.scene, item.id)


def execute_schedule(store: Store, config: LabConfig, name: Literal["schedule", "schedule_list", "schedule_cancel"],
                     arguments: dict, *, now: Callable[[], float] = time.time) -> str:
    if name == "schedule":
        args = ScheduleArguments.model_validate(arguments)
        item = create_arrangement(store, config, args, now=now)
        return "已保存安排；尚未唤醒或发送：\n" + describe(item)
    if name == "schedule_cancel":
        cancel = ScheduleCancelArguments.model_validate(arguments)
        return "已取消安排：\n" + describe(cancel_arrangement(
            store, config, id=cancel.id, requester=cancel.requester,
        ))
    page = ScheduleListArguments.model_validate(arguments)
    items = store.list_schedules(config.scene, status=page.status, offset=page.offset, limit=page.limit + 1)
    more = len(items) > page.limit
    content = "\n\n".join(describe(item) for item in items[:page.limit]) or "没有符合条件的安排。"
    if more:
        content += f"\n\n尚有记录；下页 offset={page.offset + page.limit}。列表可能随新安排或状态变化。"
    return content
