"""The configured public room endpoint is the sole source of live state."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict

USER_AGENT = 'LenBot/0.1'


class RoomInfo(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    uid: int
    room_id: int
    short_id: int
    live_status: Literal[0, 1, 2]
    title: str
    live_time: str


class RoomResponse(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    code: int
    message: str
    data: RoomInfo | None


class LiveSample(BaseModel):
    member: str
    bilibili_uid: int
    room_id: int
    requested_room_id: int
    title: str
    url: str
    is_live: bool
    started_at: str | None
    sampled_at: float


class LiveClient:
    def __init__(self, config):
        self.config = config
        self.http = httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=False,
                                     headers={'User-Agent':USER_AGENT})

    async def close(self):
        await self.http.aclose()

    async def sample(self, member, now):
        response = await self.http.get(self.config.api_url, params={'room_id': member.room_id})
        response.raise_for_status()
        envelope = RoomResponse.model_validate_json(response.content)
        if envelope.code != 0 or envelope.data is None:
            raise ValueError(f'Live source returned {envelope.code}: {envelope.message}')
        data = envelope.data
        if data.uid != member.bilibili_uid or member.room_id not in {data.room_id, data.short_id}:
            raise ValueError('Live room identity differs from the configured member')
        started_at = None
        if data.live_status == 1:
            started_at = datetime.strptime(data.live_time, '%Y-%m-%d %H:%M:%S').replace(
                tzinfo=ZoneInfo(self.config.source_timezone)).isoformat()
        return LiveSample(member=member.name, bilibili_uid=data.uid, room_id=data.room_id, requested_room_id=member.room_id,
            title=data.title, url=f'https://live.bilibili.com/{data.room_id}', is_live=data.live_status == 1,
            started_at=started_at, sampled_at=now())
