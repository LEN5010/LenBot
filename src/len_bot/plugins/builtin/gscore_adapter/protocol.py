"""Typed boundary for the GSUID Core MessageReceive/MessageSend wire frames."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class CoreMessageSegment(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    type: str
    data: Any = None


class CoreMessageSend(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    bot_id: str = 'Bot'
    bot_self_id: str = ''
    msg_id: str = ''
    target_type: str | None = None
    target_id: str | None = None
    content: list[CoreMessageSegment] | None = None
    # Core only asks for this when it wants a recall_message_id receipt.
    echo: str | None = None


class CoreMessageReceive(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)
    bot_id: str = 'Bot'
    bot_self_id: str = ''
    msg_id: str = ''
    user_type: Literal['group', 'direct', 'channel', 'sub_channel'] = 'group'
    group_id: str | int | None = None
    user_id: str | int | None = None
    sender: dict[str, Any] = Field(default_factory=dict)
    user_pm: int = 3
    content: list[CoreMessageSegment] = Field(default_factory=list)


class CoreReceipt(BaseModel):
    """The one-segment MessageReceive used to settle Core's echo token."""
    model_config = ConfigDict(extra='allow', strict=True)
    bot_id: str = 'Bot'
    bot_self_id: str = ''
    user_id: str = ''
    content: list[CoreMessageSegment]
    message: list[CoreMessageSegment] = Field(default_factory=list)
    raw_message: str = ''
