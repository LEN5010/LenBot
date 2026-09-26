"""OneBot message and text-send receipt boundaries for the new chat core."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo


SendStatus = Literal["received", "sent", "failed", "unconfirmed", "simulated"]


@dataclass(slots=True)
class Sender:
    uid: str
    nickname: str
    card: str | None
    role: str | None


@dataclass(slots=True)
class Segment:
    type: str
    data: dict[str, object]


@dataclass(slots=True)
class ChatMessage:
    id: str
    platform: Literal["qq"]
    scene: str
    platform_message_id: str | None
    sender: Sender
    time: float
    segments: list[Segment]
    reply_to: str | None
    mentions_bot: bool
    is_self: bool
    send_status: SendStatus


@dataclass(slots=True)
class SendResult:
    status: Literal["sent", "failed", "unconfirmed"]
    platform_message_id: str | None
    error: str | None


def plain_text(message: ChatMessage) -> str:
    """Current text only; media and other non-text segments remain separators."""
    return "".join(segment.data["text"] if segment.type == "text" else "\n"
                   for segment in message.segments)


def _id(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value):
        raise ValueError(f"{field} must be a nonempty OneBot ID")
    return str(value)


def parse_message(raw: dict, *, own_message_ids: set[str]) -> ChatMessage:
    """Parse one OneBot v11 message event with array-form message segments."""
    try:
        if raw["post_type"] != "message":
            raise ValueError("post_type is not message")
        kind = raw["message_type"]
        uid = _id(raw["user_id"], "user_id")
        self_id = _id(raw["self_id"], "self_id")
        message_id = _id(raw["message_id"], "message_id")
        if kind == "group":
            scene = f"group:{_id(raw['group_id'], 'group_id')}"
        elif kind == "private":
            scene = f"private:{uid}"
        else:
            raise ValueError(f"unsupported message_type {kind!r}")

        sender_raw = raw["sender"]
        if not isinstance(sender_raw, dict):
            raise ValueError("sender must be an object")
        nickname = sender_raw["nickname"]
        card = sender_raw.get("card")
        if card == "":
            card = None
        role = sender_raw.get("role")
        if not isinstance(nickname, str):
            raise ValueError("sender.nickname must be text")
        if card is not None and not isinstance(card, str):
            raise ValueError("sender.card must be text")
        if role is not None and not isinstance(role, str):
            raise ValueError("sender.role must be text")

        timestamp = raw["time"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("time must be a Unix timestamp")
        wire_segments = raw["message"]
        if not isinstance(wire_segments, list):
            raise ValueError("message must be a OneBot segment array")
        segments: list[Segment] = []
        reply_to: str | None = None
        mentions_bot = False
        for position, wire_segment in enumerate(wire_segments):
            if not isinstance(wire_segment, dict):
                raise ValueError(f"message[{position}] must be an object")
            segment_type = wire_segment["type"]
            data = wire_segment["data"]
            if not isinstance(segment_type, str) or not segment_type:
                raise ValueError(f"message[{position}].type must be nonempty text")
            if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
                raise ValueError(f"message[{position}].data must be an object")
            if segment_type == "text" and not isinstance(data["text"], str):
                raise ValueError(f"message[{position}].data.text must be text")
            if segment_type == "at":
                mentions_bot |= _id(data["qq"], f"message[{position}].data.qq") == self_id
            if segment_type == "reply" and reply_to is None:
                reply_to = _id(data["id"], f"message[{position}].data.id")
            segments.append(Segment(type=segment_type, data=dict(data)))

        return ChatMessage(
            id=str(uuid4()),
            platform="qq",
            scene=scene,
            platform_message_id=message_id,
            sender=Sender(uid=uid, nickname=nickname, card=card, role=role),
            time=float(timestamp),
            segments=segments,
            reply_to=reply_to,
            mentions_bot=mentions_bot or (reply_to is not None and reply_to in own_message_ids),
            is_self=uid == self_id,
            send_status="received",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"OneBot message parse failed: {error}; raw={repr(raw)[:500]}") from error


def _speaker(message: ChatMessage) -> str:
    if message.is_self:
        if message.send_status == "failed":
            return "我（没发出去）"
        if message.send_status == "unconfirmed":
            return "我（发送结果未确认）"
        if message.send_status == "simulated":
            return "我（模拟）"
        return "我"
    name = message.sender.card or message.sender.nickname
    return f"{name}(QQ {message.sender.uid})"


def _body(segments: list[Segment]) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.type == "reply":
            continue
        if segment.type == "text":
            parts.append(segment.data["text"])
        elif segment.type == "at":
            qq = segment.data["qq"]
            parts.append("@全体成员" if qq == "all" else f"@QQ {qq}")
        else:
            identifiers = [
                f"{field}={segment.data[field]}"
                for field in ("file", "file_id", "id", "name", "summary")
                if field in segment.data
            ]
            details = "：" + "，".join(identifiers) if identifiers else ""
            parts.append(f"[{segment.type}{details}]")
    return "".join(parts)


def render_message(message: ChatMessage, *, timezone: str, reply: ChatMessage | None = None) -> str:
    """Render a single message with its actual sender, words, and known reply."""
    clock = datetime.fromtimestamp(message.time, ZoneInfo(timezone)).isoformat(sep=" ", timespec="seconds")
    quote = ""
    if message.reply_to is not None:
        if reply is None:
            quote = f"（回复消息 {message.reply_to}）"
        else:
            quote = f"（回复 {_speaker(reply)}：{_body(reply.segments)[:40]}）"
    return f"[{clock}] {_speaker(message)}：{quote}{_body(message.segments)}"


def parse_send_result(raw: dict) -> SendResult:
    """Interpret a text-message send response, never a file-upload receipt."""
    if not isinstance(raw, dict):
        raise ValueError(f"OneBot text send response must be an object; raw={repr(raw)[:500]}")
    if raw.get("status") == "failed":
        wording = raw.get("wording")
        error = wording if isinstance(wording, str) and wording else repr(raw)[:500]
        return SendResult(status="failed", platform_message_id=None, error=error)
    if raw.get("status") == "ok" and type(raw.get("retcode")) is int and raw["retcode"] == 0:
        data = raw.get("data")
        if isinstance(data, dict) and "message_id" in data:
            message_id = data["message_id"]
            if not isinstance(message_id, bool) and isinstance(message_id, (int, str)) and str(message_id):
                return SendResult(status="sent", platform_message_id=str(message_id), error=None)
    return SendResult(status="unconfirmed", platform_message_id=None, error=repr(raw)[:500])
