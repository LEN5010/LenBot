"""Parse OneBot envelopes once into platform message records."""

from datetime import datetime, timezone
import re
from uuid import uuid4

from .messages import ChatMessage, Notice, Segment, Sender, SendResult, UploadResult


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
    if kind == "group_ban":
        duration = raw.get("duration")
        if (sub_type not in {"ban", "lift_ban"} or type(duration) is not int or duration < 0):
            raise ValueError(f"OneBot group_ban invalid sub_type/duration: {repr(raw)[:300]}")
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
    scene = (f"onebot:group:{ids['group_id']}" if ids["group_id"] is not None
             else f"onebot:private:{ids['user_id']}" if ids["user_id"] is not None else None)
    if scene is None:
        return None
    return Notice(scene=scene, notice_type=kind, sub_type=sub_type, user_id=None if ids["user_id"] is None else "onebot:" + ids["user_id"],
                  operator_id=None if ids["operator_id"] is None else "onebot:" + ids["operator_id"],
                  time=float(moment), raw=raw,
                  platform_message_id=str(raw["message_id"]) if kind in {"group_recall", "friend_recall"} else None,
                  duration=raw["duration"] if kind == "group_ban" else None)


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
            scene = f"onebot:group:{_id(raw['group_id'], 'group_id')}"
        elif kind == "private":
            scene = f"onebot:private:{uid}"
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
            segments.append(normalized_segment(segment_type, data))

        return ChatMessage(
            id=str(uuid4()),
            platform="onebot",
            bot_id="onebot:" + self_id,
            scene=scene,
            platform_message_id=message_id,
            sender=Sender(uid="onebot:" + uid, nickname=nickname, card=card, role=role),
            time=float(timestamp),
            segments=segments,
            reply_to=reply_to,
            mentions_bot=mentions_bot or (reply_to is not None and reply_to in own_message_ids),
            is_self=uid == self_id,
            send_status="received",
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
        raise ValueError(f"OneBot message parse failed: {error}; raw={repr(raw)[:500]}") from error


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


def parse_event(raw: dict) -> ChatMessage | Notice | None:
    kind = raw['post_type']
    if kind == 'message':
        return parse_message(raw, own_message_ids=set())
    if kind == 'notice':
        return parse_notice(raw)
    return None


def normalized_segment(kind: str, data: dict) -> Segment:
    if kind == 'at':
        return Segment('mention', {'user': 'all' if data['qq'] == 'all' else 'onebot:' + str(data['qq'])})
    return Segment('audio' if kind == 'record' else kind, dict(data))
