"""Platform message records, action receipts and readable conversation rendering."""

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Sequence
import json
from typing import Literal, Mapping
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
    platform: str
    bot_id: str
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
    """One platform notice routed to a configured scene; ``raw`` keeps every original field."""
    scene: str
    notice_type: str
    sub_type: str | None
    user_id: str | None
    operator_id: str | None
    time: float
    raw: Mapping[str, object]
    platform_message_id: str | None
    duration: int | None


@dataclass(slots=True)
class SendResult:
    status: Literal["sent", "failed", "unconfirmed"]
    platform_message_id: str | None
    error: str | None


@dataclass(slots=True)
class UploadResult:
    """An upload action receipt, not proof that a platform client received the file."""

    status: Literal["uploaded", "failed", "unconfirmed"]
    file_id: str | None
    error: str | None
    raw: dict | None


def plain_text(message: ChatMessage) -> str:
    """Current text only; media and other non-text segments remain separators."""
    return "".join(segment.data["text"] if segment.type == "text" else "\n"
                   for segment in message.segments)


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
    return f"{name}({message.sender.uid})" if name else f"{message.sender.uid}"


def _body(segments: list[Segment], audio: dict[int, str] | None = None) -> str:
    parts: list[str] = []
    image_index = 0
    audio_index = 0
    for segment in segments:
        if segment.type == "reply":
            continue
        if segment.type == "text":
            parts.append(segment.data["text"])
        elif segment.type == "mention":
            user = segment.data["user"]
            parts.append("[提及全体成员]" if user == "all" else f"[提及 {user}]")
        elif segment.type == "image":
            image_index += 1
            summary = segment.data.get("summary")
            details = "" if summary is None else f"：{summary}"
            parts.append(f"[图片{image_index}{details}]")
        elif segment.type == "audio":
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
        speaker = (f"{message.sender.uid}" if name is None else
                   f"{json.dumps(name, ensure_ascii=False)}({message.sender.uid})")
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
