"""Low-frequency queries to the live OneBot platform: merged forwards, group members and scene names."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from string import Template
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .messages import Segment, render_body
from .onebot_messages import normalized_segment
from ..storage.store import Store, encode
from ..prompt_files import read_prompt


PlatformCall = Callable[[str, dict], Awaitable[dict]]
PAGE_CHARS = 4000
FORWARD_DEPTH = 3
FORWARD_NODES = 300
ROLES = ("owner", "admin", "member")


class OpenForwardArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message: str = Field(min_length=1)
    forward: int = Field(default=1, gt=0)
    offset: int = Field(default=0, ge=0)

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be a nonblank platform message ID")
        return value


class MemberInfoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    user: str = Field(pattern=r"^onebot:[1-9][0-9]*$")


OPEN_FORWARD_TOOL = {"type": "function", "function": {
    "name": "open_forward", "description": "展开本场景已保存消息里的合并转发；message 是平台消息 ID，"
    "forward 是该消息第几个转发段，offset 是字符位置，每页 4000 字。嵌套转发最多展开 3 层。",
    "parameters": OpenForwardArguments.model_json_schema(),
}}
MEMBER_INFO_TOOL = {"type": "function", "function": {
    "name": "member_info", "description": "查询本群一名成员的平台当前资料（昵称、群名片、身份、头衔、入群与最后发言时间）；"
    "user 是实际平台账号，例如 onebot:70001。",
    "parameters": MemberInfoArguments.model_json_schema(),
}}


def _id(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value):
        raise ValueError(f"{field} must be a nonempty OneBot ID")
    return str(value)


def _optional_text(value: object, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value


def _succeeded(raw: object, action: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{action} response must be an object: {repr(raw)[:500]}")
    if raw.get("status") != "ok" or raw.get("retcode") != 0:
        raise ValueError(f"{action} 失败：status={raw.get('status')!r} retcode={raw.get('retcode')!r} "
                         f"wording={raw.get('wording', raw.get('msg'))!r}；raw={repr(raw)[:500]}")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"{action} data must be an object; raw={repr(raw)[:500]}")
    return data


@dataclass(frozen=True, slots=True)
class ForwardNode:
    uid: str
    name: str | None
    time: float
    segments: list[Segment]


def parse_forward(raw: object) -> list[ForwardNode]:
    """Parse NapCat's get_forward_msg ``data.messages`` array once at the platform boundary."""
    try:
        items = _succeeded(raw, "get_forward_msg")["messages"]
        if not isinstance(items, list):
            raise ValueError("data.messages must be an array")
        nodes = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item["sender"], dict):
                raise ValueError(f"messages[{index}] must be an object with a sender object")
            sender = item["sender"]
            card = _optional_text(sender.get("card"), f"messages[{index}].sender.card")
            nickname = _optional_text(sender.get("nickname"), f"messages[{index}].sender.nickname")
            time = item["time"]
            if isinstance(time, bool) or not isinstance(time, (int, float)):
                raise ValueError(f"messages[{index}].time must be a Unix timestamp")
            wire = item["message"]
            if not isinstance(wire, list):
                raise ValueError(f"messages[{index}].message must be a segment array")
            segments = []
            for position, segment in enumerate(wire):
                if (not isinstance(segment, dict) or not isinstance(segment["type"], str) or not segment["type"]
                        or not isinstance(segment["data"], dict)):
                    raise ValueError(f"messages[{index}].message[{position}] must have type text and data object")
                if segment["type"] == "text" and not isinstance(segment["data"]["text"], str):
                    raise ValueError(f"messages[{index}].message[{position}].data.text must be text")
                segments.append(normalized_segment(segment["type"], segment["data"]))
            nodes.append(ForwardNode("onebot:" + _id(sender["user_id"], f"messages[{index}].sender.user_id"),
                                     card or nickname, float(time), segments))
        return nodes
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"get_forward_msg 返回无法解析：{error}; raw={repr(raw)[:500]}") from error


async def _expand(call: PlatformCall, forward_id: str, timezone: str, depth: int,
                  budget: list[int], lines: list[str]) -> int:
    nodes = parse_forward(await call("get_forward_msg", {"id": forward_id}))
    indent = "  " * (depth - 1)
    zone = ZoneInfo(timezone)
    for number, node in enumerate(nodes, start=1):
        if budget[0] == 0:
            lines.append(f"{indent}[其余 {len(nodes) - number + 1} 条未展开：已达 {FORWARD_NODES} 条上限]")
            break
        budget[0] -= 1
        clock = datetime.fromtimestamp(node.time, zone).isoformat(sep=" ", timespec="seconds")
        speaker = f"{node.name}({node.uid})" if node.name else f"{node.uid}"
        lines.append(f"{indent}[{number}] [{clock}] {speaker}：")
        lines.extend(f"{indent}  {line}" for line in render_body(node.segments).splitlines())
        for segment in (item for item in node.segments if item.type == "forward"):
            if budget[0] == 0:
                lines.append(f"{indent}  [其余嵌套转发未展开：已达 {FORWARD_NODES} 条上限]")
                break
            nested = segment.data.get("id")
            if nested is None:
                lines.append(f"{indent}  [嵌套转发没有平台 id，未展开]")
            elif depth == FORWARD_DEPTH:
                lines.append(f"{indent}  [嵌套转发 id={nested}：超过 {FORWARD_DEPTH} 层未展开]")
            else:
                lines.append(f"{indent}  嵌套转发 id={nested}：")
                await _expand(call, _id(nested, "forward.data.id"), timezone, depth + 1, budget, lines)
    return len(nodes)


async def open_forward(store: Store, scene: str, timezone: str, args: OpenForwardArguments,
                       call: PlatformCall) -> str:
    message = store.find_message(scene, args.message)
    if message is None:
        raise ValueError(f"当前场景没有平台消息 {args.message}")
    forwards = [segment for segment in message.segments if segment.type == "forward"]
    if args.forward > len(forwards):
        raise ValueError(f"消息 {args.message} 只有 {len(forwards)} 个合并转发段，没有第 {args.forward} 个")
    forward_id = _id(forwards[args.forward - 1].data.get("id"), "forward.data.id")
    lines: list[str] = []
    count = await _expand(call, forward_id, timezone, 1, [FORWARD_NODES], lines)
    text = "\n".join(lines)
    if args.offset > len(text):
        raise ValueError(f"offset {args.offset} exceeds forward text length {len(text)}")
    end = min(args.offset + PAGE_CHARS, len(text))
    result = {"message": args.message, "forward": args.forward, "forward_id": forward_id,
              "top_level_items": count, "offset": args.offset, "total_chars": len(text),
              "next_offset": None if end == len(text) else end, "text": text[args.offset:end]}
    return Template(read_prompt("next_forward.md")).substitute(
        scene=scene, result=encode(result)).strip()


async def scene_title(scene: str, call: PlatformCall) -> tuple[str, int | None]:
    """The group name and member count, or the private contact's nickname, for the panel."""
    platform, kind, number = scene.split(":", 2)
    action, field = ("get_group_info", "group_name") if kind == "group" else ("get_stranger_info", "nickname")
    raw = await call(action, {"group_id" if kind == "group" else "user_id": int(number)})
    try:
        data = _succeeded(raw, action)
        title = data[field]
        if not isinstance(title, str):
            raise ValueError(f"{field} must be text")
        if kind != "group":
            return title, None
        members = data["member_count"]
        if type(members) is not int or members < 0:
            raise ValueError("member_count must be a non-negative integer")
        return title, members
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{action} 返回无法解析：{error}; raw={repr(raw)[:500]}") from error


def parse_member(raw: object, *, group_id: str, qq: str) -> dict:
    """Parse get_group_member_info once; identity fields must match the request."""
    try:
        data = _succeeded(raw, "get_group_member_info")
        if _id(data["group_id"], "group_id") != group_id or _id(data["user_id"], "user_id") != qq:
            raise ValueError(f"response is for group {data['group_id']!r} user {data['user_id']!r}")
        role = data["role"]
        if role not in ROLES:
            raise ValueError(f"role must be one of {list(ROLES)}: {role!r}")
        result = {"user": "onebot:" + qq, "nickname": data["nickname"], "card": data["card"], "role": role}
        for field in ("nickname", "card"):
            if not isinstance(result[field], str):
                raise ValueError(f"{field} must be text")
        for field in ("join_time", "last_sent_time"):
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{field} must be an integer Unix timestamp")
            result[field] = value
        for field in ("title", "level"):
            if field in data:
                result[field] = _optional_text(data[field], field)
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"get_group_member_info 返回无法解析：{error}; raw={repr(raw)[:500]}") from error


async def member_info(scene: str, timezone: str, args: MemberInfoArguments, call: PlatformCall) -> str:
    platform, kind, group_id = scene.split(":", 2)
    if kind != "group":
        raise ValueError("member_info 只在群场景可用")
    raw = await call("get_group_member_info", {"group_id": int(group_id), "user_id": int(args.user.split(":", 1)[1]), "no_cache": True})
    member = parse_member(raw, group_id=group_id, qq=args.user.split(":", 1)[1])
    zone = ZoneInfo(timezone)
    for field in ("join_time", "last_sent_time"):
        member[field] = datetime.fromtimestamp(member[field], zone).isoformat(sep=" ", timespec="seconds")
    return Template(read_prompt("next_member_info.md")).substitute(
        scene=scene, result=encode(member)).strip()
