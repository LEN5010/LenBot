"""Project a new-core message into one old message Event, without replaying work."""

from __future__ import annotations

import math
import re

from len_bot.events.models import Event, EventType

from .messages import ChatMessage, Segment


_QQ = re.compile(r"[1-9][0-9]*\Z")


def _qq(value: object, field: str) -> str:
    if not isinstance(value, str) or _QQ.fullmatch(value) is None:
        raise ValueError(f"{field} must be a QQ number as text")
    return value


def _platform_id(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty platform message ID or null")
    return value


def _projection(segments: list[Segment]) -> str:
    """Use the old OneBot entrance's readable projection, not a claimed wire copy."""
    parts: list[str] = []
    for segment in segments:
        if segment.type == "text":
            value = segment.data["text"]
            if not isinstance(value, str):
                raise ValueError("text segment data.text must be text")
            parts.append(value)
        elif segment.type == "at":
            parts.append(f"[CQ:at,qq={segment.data['qq']}]")
        elif segment.type == "reply":
            parts.append(f"[CQ:reply,id={segment.data['id']}]")
        elif segment.type == "image":
            parts.append("[图片]")
        else:
            parts.append(f"[{segment.type}]")
    return "".join(parts)


def _raw_text(message: ChatMessage, raw: dict | None) -> str:
    if raw is not None:
        if not isinstance(raw, dict):
            raise ValueError("raw must be an object or null")
        if "raw_message" in raw:
            value = raw["raw_message"]
            if not isinstance(value, str):
                raise ValueError("raw.raw_message must be text when present")
            return value
    return _projection(message.segments)


def _outgoing_segments(message: ChatMessage) -> list[dict]:
    parts: list[dict] = []
    for position, segment in enumerate(message.segments):
        if segment.type == "text":
            value = segment.data["text"]
            if not isinstance(value, str):
                raise ValueError(f"segments[{position}].data.text must be text")
            parts.append({"type": "text", "text": value})
        elif segment.type == "at":
            qq = segment.data["qq"]
            if qq == "all":
                parts.append({"type": "at_all"})
            else:
                parts.append({"type": "at", "qq_uid": _qq(str(qq), f"segments[{position}].data.qq")})
        elif segment.type == "reply":
            ident = segment.data["id"]
            if message.reply_to is None or str(ident) != message.reply_to:
                raise ValueError(f"segments[{position}] reply differs from message.reply_to")
        else:
            raise ValueError(f"unsupported outgoing segment type {segment.type!r} at {position}")
    return parts


def convert_next_message(message: ChatMessage, raw: dict | None, *, bot_qq: str,
                         reply_is_self: bool) -> Event:
    """Convert one saved message; the caller owns source rows and destination transactions."""
    try:
        if not isinstance(message.id, str) or not message.id:
            raise ValueError("message.id must be nonempty text")
        if message.platform != "qq":
            raise ValueError(f"unsupported platform {message.platform!r}")
        scene_kind, separator, scene_qq = message.scene.partition(":")
        if not separator or scene_kind not in {"group", "private"}:
            raise ValueError(f"invalid QQ scene {message.scene!r}")
        _qq(scene_qq, "message.scene QQ")
        uid = _qq(message.sender.uid, "message.sender.uid")
        if isinstance(message.time, bool) or not isinstance(message.time, (int, float)) or not math.isfinite(message.time):
            raise ValueError("message.time must be a finite Unix timestamp")
        platform_id = _platform_id(message.platform_message_id, "message.platform_message_id")
        reply_to = _platform_id(message.reply_to, "message.reply_to")
        raw_text = _raw_text(message, raw)
        actor = f"user:{uid}"

        if not message.is_self:
            if uid == bot_qq or message.send_status != "received":
                raise ValueError("non-self message must be a received message from another QQ")
            if scene_kind == "private" and scene_qq != uid:
                raise ValueError("private scene QQ differs from received sender QQ")
            sender = {"nickname": message.sender.nickname, "card": message.sender.card,
                      "role": message.sender.role}
            segments = [{"type": item.type, "data": dict(item.data)} for item in message.segments]
            at_bot = any(item.type == "at" and str(item.data["qq"]) == bot_qq
                         for item in message.segments)
            return Event(
                id=message.id,
                event_type=(EventType.GROUP_MESSAGE_RECEIVED if scene_kind == "group"
                            else EventType.PRIVATE_MESSAGE_RECEIVED),
                scene_id=message.scene, actor_id=actor, timestamp=message.time,
                payload={"message_id": platform_id, "reply_to_message_id": reply_to,
                         "raw_text": raw_text, "at_bot": at_bot,
                         "reply_bot": reply_to is not None and reply_is_self,
                         "sender": sender, "segments": segments},
            )

        if uid != bot_qq:
            raise ValueError("self message sender QQ differs from configured bot_qq")
        if message.send_status not in {"received", "sent", "failed", "unconfirmed", "simulated"}:
            raise ValueError(f"unsupported self send_status {message.send_status!r}")
        if message.send_status in {"received", "sent"} and platform_id is None:
            raise ValueError("confirmed self message requires a platform message ID")
        if message.send_status == "simulated" and platform_id is not None:
            raise ValueError("simulated expression cannot claim a platform message ID")
        segments = _outgoing_segments(message)
        if message.send_status in {"received", "sent", "simulated"}:
            simulated = message.send_status == "simulated"
            return Event(
                id=message.id, event_type=EventType.MESSAGE_SENT,
                scene_id=message.scene, actor_id=actor, timestamp=message.time,
                payload={"message_id": platform_id, "reply_to": reply_to,
                         "raw_text": raw_text, "content": raw_text, "segments": segments,
                         "origin_mode": "simulated" if simulated else "live",
                         "delivery_status": "sent", "delivery_unknown": False},
                metadata={"simulated": True, "conversation_excluded": True} if simulated else {},
            )

        unknown = message.send_status == "unconfirmed"
        return Event(
            id=message.id, event_type=EventType.MESSAGE_SEND_FAILED,
            scene_id=message.scene, actor_id=actor, timestamp=message.time,
            payload={"message_id": platform_id, "reply_to": reply_to,
                     "raw_text": raw_text, "attempted_text": raw_text, "segments": segments,
                     "origin_mode": "live", "delivery_status": "unknown" if unknown else "not_sent",
                     "delivery_unknown": unknown},
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"Next message rollback conversion failed: {error}; "
            f"message={repr(message)[:500]}; raw={repr(raw)[:300]}"
        ) from error
