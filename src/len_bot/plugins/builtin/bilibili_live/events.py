"""Typed source facts owned by the live plugin."""
from pydantic import BaseModel, ConfigDict


class LiveSample(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    member: str
    bilibili_uid: int
    room_id: int
    requested_room_id: int
    title: str
    url: str
    is_live: bool
    started_at: str | None
    sampled_at: float
    # The room endpoint already returns these; the card shows one of them and
    # falls back to the keyframe when the room has no custom cover.
    cover_url: str = ''
    supersedes_action_id: str | None = None


class LiveEndedSample(LiveSample):
    ended_session_started_at: str
