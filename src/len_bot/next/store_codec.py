"""JSON encoding and message reconstruction for the current stored record format."""

import json

from .messages import ChatMessage, Segment, Sender


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def decode_message(body: str) -> ChatMessage:
    value = json.loads(body)
    value["sender"] = Sender(**value["sender"])
    value["segments"] = [Segment(**segment) for segment in value["segments"]]
    return ChatMessage(**value)
