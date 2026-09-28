"""Scene-local literal history search and complete, paged message reading."""

from datetime import datetime
from pathlib import Path
from string import Template
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .messages import ChatMessage, render_message
from .store import Store, encode
from .audio_store import AudioStore


class RecallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["search", "recent", "read", "context"] = "search"
    query: str | None = None
    who: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    after: datetime | None = None
    before: datetime | None = None
    snapshot: int | None = Field(default=None, ge=0)
    record: int | None = Field(default=None, gt=0)
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("after", "before", mode="before")
    @classmethod
    def explicit_time(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("time must be an ISO string with a timezone offset")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("time must include a timezone offset")
        return parsed

    @model_validator(mode="after")
    def operation_fields(self) -> "RecallArguments":
        if self.action == "search":
            if self.record is not None:
                raise ValueError("search does not accept record")
            if self.offset and self.snapshot is None:
                raise ValueError("search continuation requires its original snapshot")
            if self.after is not None and self.before is not None and self.after >= self.before:
                raise ValueError("after must be earlier than before")
        elif self.action == "recent":
            if any(value is not None for value in (self.query, self.who, self.after, self.before, self.record)):
                raise ValueError("recent accepts only snapshot and offset")
            if self.offset and self.snapshot is None:
                raise ValueError("recent continuation requires its original snapshot")
        else:
            if self.record is None:
                raise ValueError(f"{self.action} requires record from the history result")
            if any(value is not None for value in (self.query, self.who, self.after, self.before, self.snapshot)):
                raise ValueError(f"{self.action} does not accept search filters or snapshot")
            if self.action == "context" and self.offset:
                raise ValueError("context does not accept offset; use read for a long message")
        return self


RECALL_TOOL = {"type": "function", "function": {
    "name": "recall_chat",
    "description": "查当前场景历史原话。search按消息时间升序，每页10条；recent按落库位置取最新10条，"
                   "页内正序，向前续页；两者续页均带原snapshot和offset。read用已有record与字符offset"
                   "分段读全文；context查看前后各3条。who是实际QQ，时间须含时区。",
    "parameters": RecallArguments.model_json_schema(),
}}
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_recall.md"


def message_page(record: int, message: ChatMessage, timezone: str, *, offset: int, size: int,
                 audio: dict[int, str] | None = None) -> dict:
    # Keep the original reply reference instead of expanding a later-arriving quote.
    text = render_message(message, timezone=timezone)
    if offset > len(text):
        raise ValueError(f"offset {offset} exceeds message length {len(text)}")
    end = min(offset + size, len(text))
    return {"record": record, "platform_message_id": message.platform_message_id,
            "sender_qq": message.sender.uid, "send_status": message.send_status,
            "time": datetime.fromtimestamp(message.time, ZoneInfo(timezone)).isoformat(),
            "is_self": message.is_self,
            "audio_descriptions": audio if offset == 0 else None,
            "offset": offset, "total_chars": len(text),
            "next_offset": end if end < len(text) else None, "text": text[offset:end]}


def recall_chat(store: Store, scene: str, timezone: str, arguments: RecallArguments) -> str:
    def render_page(seq, message, *, offset=0, size=160):
        return message_page(seq, message, timezone, offset=offset, size=size,
                            audio=AudioStore(store).captions(scene, message.platform_message_id))
    if arguments.action in {"search", "recent"}:
        current = store.max_message_seq(scene)
        snapshot = current if arguments.snapshot is None else arguments.snapshot
        if snapshot > current:
            raise ValueError(f"snapshot {snapshot} exceeds current scene position {current}")
        if arguments.action == "search":
            rows = store.search_messages(
                scene, query=arguments.query, who=arguments.who,
                after=None if arguments.after is None else arguments.after.timestamp(),
                before=None if arguments.before is None else arguments.before.timestamp(),
                snapshot=snapshot, offset=arguments.offset, limit=11,
            )
            page = rows[:10]
        else:
            rows = store.recent_records(scene, limit=11, snapshot=snapshot, offset=arguments.offset)
            page = rows[-10:]
        result = {"action": arguments.action, "snapshot": snapshot, "offset": arguments.offset,
                  "next_offset": arguments.offset + 10 if len(rows) > 10 else None,
                  "previews": [render_page(seq, message)
                               for seq, message in page]}
    elif arguments.action == "read":
        message = store.read_message(scene, arguments.record)
        if message is None:
            raise ValueError(f"当前场景没有消息记录 {arguments.record}")
        result = {"action": "read", **render_page(arguments.record, message, offset=arguments.offset, size=4000)}
    else:
        rows = store.context_messages(scene, arguments.record)
        result = {"action": "context", "center": arguments.record,
                  "previews": [render_page(seq, message)
                               for seq, message in rows]}
    return Template(PROMPT.read_text()).substitute(scene=scene, result=encode(result))
