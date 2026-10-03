"""OneBot messages and message/file action receipts for the new chat core."""

from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from typing import Literal, Mapping
import re
from uuid import uuid4
from zoneinfo import ZoneInfo


SendStatus = Literal["received", "sent", "failed", "unconfirmed", "simulated"]


@dataclass(slots=True)
class Sender:
    uid: str
    nickname: str | None
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
    recalled: bool = False


@dataclass(frozen=True)
class Notice:
    """One OneBot notice routed to a configured scene; ``raw`` keeps every original field."""
    scene: str
    notice_type: str
    sub_type: str | None
    user_id: str | None
    operator_id: str | None
    time: float
    raw: Mapping[str, object]


def parse_notice(raw: dict) -> Notice | None:
    """Parse the fields every routed notice needs; ``None`` when it names no scene."""
    kind, sub_type = raw.get("notice_type"), raw.get("sub_type")
    if not isinstance(kind, str) or not kind or (sub_type is not None and not isinstance(sub_type, str)):
        raise ValueError(f"OneBot notice lacks a text notice_type/sub_type: {repr(raw)[:300]}")
    moment = raw.get("time")
    if isinstance(moment, bool) or not isinstance(moment, int | float):
        raise ValueError(f"OneBot notice lacks numeric time: {repr(raw)[:300]}")
    try:
        datetime.fromtimestamp(moment, timezone.utc)
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError(f"OneBot notice invalid time: {error}; raw={repr(raw)[:300]}") from error
    if kind in {"group_recall", "friend_recall"}:
        _id(raw.get("message_id"), "message_id")
    ids = {}
    # Implementations report operator_id 0 when there is no separate operator; keep it as sent.
    for name, pattern in (("group_id", r"[1-9][0-9]*"), ("user_id", r"[1-9][0-9]*"), ("operator_id", r"[0-9]+")):
        value = raw.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | str)
                                  or re.fullmatch(pattern, str(value)) is None):
            raise ValueError(f"OneBot notice {name} is not a QQ number: {repr(raw)[:300]}")
        ids[name] = None if value is None else str(value)
    if kind.startswith("group_") and ids["group_id"] is None:
        raise ValueError(f"OneBot {kind} requires group_id; raw={repr(raw)[:300]}")
    if kind == "friend_recall" and (ids["user_id"] is None or ids["group_id"] is not None):
        raise ValueError(f"OneBot friend_recall requires private user_id without group_id; raw={repr(raw)[:300]}")
    scene = (f"group:{ids['group_id']}" if ids["group_id"] is not None
             else f"private:{ids['user_id']}" if ids["user_id"] is not None else None)
    if scene is None:
        return None
    return Notice(scene=scene, notice_type=kind, sub_type=sub_type, user_id=ids["user_id"],
                  operator_id=ids["operator_id"], time=float(moment), raw=raw)


@dataclass(slots=True)
class SendResult:
    status: Literal["sent", "failed", "unconfirmed"]
    platform_message_id: str | None
    error: str | None


@dataclass(slots=True)
class UploadResult:
    """An upload action receipt, not proof that a QQ client received the file."""

    status: Literal["uploaded", "failed", "unconfirmed"]
    file_id: str | None
    error: str | None
    raw: dict | None


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
        datetime.fromtimestamp(timestamp, timezone.utc)
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
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
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
    return f"{name}(QQ {message.sender.uid})" if name else f"QQ {message.sender.uid}"


def _body(segments: list[Segment], audio: dict[int, str] | None = None) -> str:
    parts: list[str] = []
    image_index = 0
    audio_index = 0
    for segment in segments:
        if segment.type == "reply":
            continue
        if segment.type == "text":
            parts.append(segment.data["text"])
        elif segment.type == "at":
            qq = segment.data["qq"]
            parts.append("[提及全体成员]" if qq == "all" else f"[提及 QQ {qq}]")
        elif segment.type == "image":
            image_index += 1
            summary = segment.data.get("summary")
            details = "" if summary is None else f"：{summary}"
            parts.append(f"[图片{image_index}{details}]")
        elif segment.type == "record":
            audio_index += 1
            description = None if audio is None else audio.get(audio_index)
            details = "" if description is None else "：" + description
            parts.append(f"[语音{audio_index}{details}]")
        else:
            identifiers = [
                f"{field}={segment.data[field]}"
                for field in ("file", "file_id", "id", "name", "summary")
                if field in segment.data
            ]
            details = "：" + "，".join(identifiers) if identifiers else ""
            parts.append(f"[{segment.type}{details}]")
    return "".join(parts)


def render_body(segments: list[Segment]) -> str:
    """Segment text and placeholders exactly as in rendered chat lines."""
    return _body(segments)


def render_message(message: ChatMessage, *, timezone: str, reply: ChatMessage | None = None,
                   audio: dict[int, str] | None = None) -> str:
    """Single-message callers share the batch's identity, quote and receipt semantics."""
    return render_batch([(message, reply, {} if audio is None else audio)], timezone=timezone)


def render_text(message: ChatMessage, *, reply: ChatMessage | None = None,
                audio: dict[int, str] | None = None) -> str:
    """The quoted reply and words of one message, without time or sender."""
    quote = ""
    if message.reply_to is not None:
        if reply is None:
            quote = f"（回复消息 {message.reply_to}）"
        else:
            quote = (f"（回复消息 {message.reply_to}，{'已撤回 · ' if reply.recalled else ''}"
                     f"{_speaker(reply)}，节选：{_body(reply.segments)[:40]}）")
    return f"{quote}{_body(message.segments, audio)}"


def render_batch(items: Sequence[tuple[ChatMessage, ChatMessage | None, dict[int, str]]], *,
                 timezone: str, reason: str | None = None) -> str:
    """A persisted batch: real identifiers, one date/zone header, indented words."""
    lines = [] if reason is None else [reason]
    identifiers = {(message.scene, message.platform_message_id) for message, _, _ in items
                   if message.platform_message_id is not None}
    previous = None
    for message, reply, audio in items:
        local = datetime.fromtimestamp(message.time, ZoneInfo(timezone))
        header = (message.scene, local.date(), local.strftime('%z'))
        if header != previous:
            lines.append(f"{message.scene}｜{local.date()}｜{timezone} {local.strftime('%z')}")
            previous = header
        # Names and IDs are data on one line, not new batch headings.
        name = '我' if message.is_self else message.sender.card or message.sender.nickname
        speaker = (f"QQ {message.sender.uid}" if name is None else
                   f"{json.dumps(name, ensure_ascii=False)}(QQ {message.sender.uid})")
        labels = []
        if message.platform_message_id is not None:
            labels.append('id=' + json.dumps(message.platform_message_id, ensure_ascii=False))
        if message.reply_to is not None:
            labels.append('回复=' + json.dumps(message.reply_to, ensure_ascii=False))
        if message.send_status != 'received':
            labels.append('状态=' + message.send_status)
        if message.recalled:
            labels.append('已撤回')
        suffix = '' if not labels else ' [' + '，'.join(labels) + ']'
        lines.append(f"{local.strftime('%H:%M:%S')} {speaker}{suffix}")
        if reply is not None and (reply.scene, reply.platform_message_id) not in identifiers:
            excerpt = json.dumps(_body(reply.segments)[:40], ensure_ascii=False)
            who = json.dumps(_speaker(reply), ensure_ascii=False)
            lines.append(f"  引用节选 {who}{'（已撤回）' if reply.recalled else ''}：{excerpt}")
        lines.extend('  ' + line for line in _body(message.segments, audio).split('\n'))
    return '\n'.join(lines)


def parse_send_result(raw: dict) -> SendResult:
    """Interpret a message send response, never a file-upload receipt."""
    if not isinstance(raw, dict):
        raise ValueError(f"OneBot message send response must be an object; raw={repr(raw)[:500]}")
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


def parse_upload_result(raw: dict) -> UploadResult:
    """Parse the selected NapCat upload action without inventing a message ID."""
    if not isinstance(raw, dict):
        raise ValueError(f"OneBot upload response must be an object; raw={raw!r}")
    status = raw.get("status")
    retcode = raw.get("retcode")
    if status == "failed" and type(retcode) is int and retcode != 0:
        wording = raw.get("wording")
        error = wording if isinstance(wording, str) and wording else repr(raw)
        return UploadResult("failed", None, error, raw)
    if status == "ok" and type(retcode) is int and retcode == 0:
        data = raw.get("data")
        file_id = data.get("file_id") if isinstance(data, dict) else None
        if isinstance(file_id, str) and file_id.strip():
            return UploadResult("uploaded", file_id, None, raw)
    return UploadResult("unconfirmed", None, repr(raw), raw)
