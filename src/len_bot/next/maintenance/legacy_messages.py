"""Convert an already decoded legacy message event for an offline history import."""

from __future__ import annotations

import math
import re

from ..platform.messages import ChatMessage, Segment, Sender


_QQ = re.compile(r"[1-9][0-9]*\Z")
_INCOMING = {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED"}
_OUTGOING = {"MESSAGE_SENT", "MESSAGE_SEND_FAILED"}


def _qq(value: object, field: str) -> str:
    if not isinstance(value, str) or _QQ.fullmatch(value) is None:
        raise ValueError(f"{field} must be an actual QQ number as text")
    return value


def _platform_id(value: object, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value):
        raise ValueError(f"{field} must be a nonempty platform message ID or null")
    return str(value)


def _text_only(raw_text: str) -> list[Segment]:
    # This is old saved text, not a reconstructed OneBot segment array.
    return [Segment(type="text", data={"text": raw_text, "legacy_text_only": True})]


def _incoming_segments(raw: object, raw_text: str) -> list[Segment]:
    if raw is None:
        return _text_only(raw_text)
    if not isinstance(raw, list):
        raise ValueError("incoming segments must be an array or null")
    if not raw and raw_text:
        raise ValueError("incoming segments are empty while raw_text has content; original segments are inconsistent")
    segments = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"incoming segments[{position}] must be an object")
        kind, data = item["type"], item["data"]
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"incoming segments[{position}].type must be nonempty text")
        if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
            raise ValueError(f"incoming segments[{position}].data must be an object")
        if kind == "text" and not isinstance(data["text"], str):
            raise ValueError(f"incoming segments[{position}].data.text must be text")
        if kind == "at" and data["qq"] != "all" and _platform_id(
                data["qq"], f"incoming segments[{position}].data.qq") is None:
            raise ValueError(f"incoming segments[{position}].data.qq must be a nonempty OneBot ID or all")
        if kind == "reply" and _platform_id(
                data["id"], f"incoming segments[{position}].data.id") is None:
            raise ValueError(f"incoming segments[{position}].data.id must be a nonempty OneBot ID")
        segments.append(Segment(type=kind, data=dict(data)))
    return segments


def _outgoing_segments(raw: object, raw_text: str) -> list[Segment]:
    if raw is None:
        return _text_only(raw_text)
    if not isinstance(raw, list):
        raise ValueError("outgoing segments must be an array or null")
    if not raw and raw_text:
        raise ValueError("outgoing segments are empty while raw_text has content; original segments are inconsistent")
    segments = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"outgoing segments[{position}] must be an object")
        kind = item["type"]
        if kind == "text":
            value = item["text"]
            if not isinstance(value, str):
                raise ValueError(f"outgoing segments[{position}].text must be text")
            segments.append(Segment(type="text", data={"text": value}))
        elif kind == "at":
            segments.append(Segment(type="at", data={"qq": _qq(item["qq_uid"], f"outgoing segments[{position}].qq_uid")}))
        elif kind == "at_all":
            segments.append(Segment(type="at", data={"qq": "all"}))
        elif kind in {"image", "video", "audio"}:
            asset_id = item["asset_id"]
            if not isinstance(asset_id, str) or not asset_id:
                raise ValueError(f"outgoing segments[{position}].asset_id must be nonempty text")
            segments.append(Segment(type=kind, data={"legacy_asset_id": asset_id}))
        else:
            raise ValueError(f"unsupported outgoing segment type {kind!r} at {position}")
    return segments


def _sender(value: object) -> tuple[str | None, str | None, str | None]:
    if not isinstance(value, dict):
        raise ValueError("sender must be an object")
    nickname, card, role = value["nickname"], value["card"], value["role"]
    for field, text in (("nickname", nickname), ("card", card), ("role", role)):
        if text is not None and not isinstance(text, str):
            raise ValueError(f"sender.{field} must be text or null")
    return nickname, card, role


def _delivery(event_type: str, payload: dict, metadata: dict) -> tuple[str, str | None]:
    simulated = metadata.get("simulated", False)
    if type(simulated) is not bool:
        raise ValueError("metadata.simulated must be boolean")
    origin = payload.get("origin_mode")
    if origin is not None and not isinstance(origin, str):
        raise ValueError("origin_mode must be text or null")
    if origin == "shadow":
        raise ValueError("shadow action is not an actually received or sent message")
    simulated = simulated or origin == "simulated"
    status = payload.get("delivery_status")
    if status is not None and status not in {"sent", "unknown", "not_sent", "rejected"}:
        raise ValueError(f"unsupported delivery_status {status!r}")
    unknown = payload.get("delivery_unknown", False)
    if type(unknown) is not bool:
        raise ValueError("delivery_unknown must be boolean")
    platform_id = _platform_id(payload.get("message_id"), "payload.message_id")
    if simulated:
        platform_id = None
    if unknown or status is None or status == "unknown" or (status == "sent" and platform_id is None and not simulated):
        return "unconfirmed", platform_id
    if status == "sent":
        if event_type != "MESSAGE_SENT":
            raise ValueError("MESSAGE_SEND_FAILED cannot claim a confirmed sent status")
        return "simulated" if simulated else "sent", platform_id
    if event_type != "MESSAGE_SEND_FAILED":
        raise ValueError(f"{event_type} cannot claim a {status} delivery status")
    return "failed", platform_id


def convert_legacy_message(event: dict, *, bot_qq: str) -> ChatMessage:
    """Map only old message facts, leaving the complete original event to the importer."""
    try:
        if not isinstance(event, dict):
            raise ValueError("legacy event must be an object")
        ident, kind, scene, actor = (event[key] for key in ("id", "event_type", "scene_id", "actor_id"))
        if not isinstance(ident, str) or not ident:
            raise ValueError("event.id must be nonempty text")
        if kind not in _INCOMING | _OUTGOING:
            raise ValueError(f"unsupported legacy message event_type {kind!r}")
        if not isinstance(scene, str) or ":" not in scene:
            raise ValueError("scene_id must be a group or private QQ scene")
        scene_kind, scene_qq = scene.split(":", 1)
        if scene_kind not in {"group", "private"}:
            raise ValueError("scene_id must be a group or private QQ scene")
        _qq(scene_qq, "scene_id QQ")
        stamp = event["timestamp"]
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise ValueError("timestamp must be a finite number")
        payload, metadata = event["payload"], event["metadata"]
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise ValueError("payload and metadata must be decoded objects")

        if kind in _INCOMING:
            expected_kind = "GROUP_MESSAGE_RECEIVED" if scene_kind == "group" else "PRIVATE_MESSAGE_RECEIVED"
            if kind != expected_kind:
                raise ValueError(f"{kind} does not match {scene}")
            if not isinstance(actor, str) or not actor.startswith("user:"):
                raise ValueError("incoming actor_id must be user:<QQ>")
            uid = _qq(actor.removeprefix("user:"), "actor_id QQ")
            if scene_kind == "private" and scene_qq != uid:
                raise ValueError("private scene QQ does not match incoming actor QQ")
            raw_text, at_bot, reply_bot = (payload[key] for key in ("raw_text", "at_bot", "reply_bot"))
            if not isinstance(raw_text, str) or type(at_bot) is not bool or type(reply_bot) is not bool:
                raise ValueError("incoming raw_text must be text and at_bot/reply_bot must be booleans")
            nickname, card, role = _sender(payload["sender"])
            return ChatMessage(
                id=ident, platform="qq", scene=scene,
                platform_message_id=_platform_id(payload.get("message_id"), "payload.message_id"),
                sender=Sender(uid=uid, nickname=nickname, card=card, role=role),
                time=float(stamp), segments=_incoming_segments(payload.get("segments"), raw_text),
                reply_to=_platform_id(payload.get("reply_to_message_id"), "payload.reply_to_message_id"),
                mentions_bot=at_bot or reply_bot, is_self=uid == bot_qq, send_status="received",
            )

        if actor not in {f"user:{bot_qq}", "bot"}:
            raise ValueError(f"outgoing actor_id must be user:{bot_qq} or legacy bot")
        if "reply_to" not in payload:
            raise ValueError("outgoing payload.reply_to is required to preserve the actual reply relation")
        raw_text = payload["raw_text"]
        if not isinstance(raw_text, str):
            raise ValueError("outgoing payload.raw_text must be text")
        status, platform_id = _delivery(kind, payload, metadata)
        segments = _outgoing_segments(payload.get("segments"), raw_text)
        return ChatMessage(
            id=ident, platform="qq", scene=scene, platform_message_id=platform_id,
            sender=Sender(uid=bot_qq, nickname=None, card=None, role=None),
            time=float(stamp), segments=segments,
            reply_to=_platform_id(payload["reply_to"], "payload.reply_to"),
            mentions_bot=False, is_self=True, send_status=status,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Legacy message conversion failed: {error}; event={repr(event)[:500]}") from error
