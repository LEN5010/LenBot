"""Bilibili Live Sensor (ADR-0021, V2 plan §十六 Plugin 1).

A pure sensory plugin: polls Bilibili's public room API and emits
LIVE_STARTED / LIVE_ENDED fact events into the runtime bus. It NEVER notifies
anyone directly — whether the bot speaks is decided by condition-bound
obligations ("开播叫我") through the standard TASK_DUE authority path.
"""

import asyncio
import logging
from typing import Any

import httpx

from len_bot.events.models import Event, EventType
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginManifest, PluginPermission, PluginType

logger = logging.getLogger(__name__)

LIVE_API_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"


class BilibiliLiveSensor(BasePlugin):
    def __init__(self):
        super().__init__(manifest=PluginManifest(
            id="bilibili_live_sensor",
            name="Bilibili 直播监控感官",
            description="轮询指定直播间状态，向事件总线投递 LIVE_STARTED / LIVE_ENDED 事实事件（不直接通知任何人）。",
            version="1.0.0",
            plugin_type=PluginType.SENSORY,
            permissions=[PluginPermission.EMIT_EVENT],
            config_schema={
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string", "title": "投递场景", "description": "事实事件进入的 scene，如 group:123"},
                    "room_ids": {"type": "array", "items": {"type": "integer"}, "title": "直播间号列表"},
                    "interval_seconds": {"type": "number", "title": "轮询间隔（秒）", "minimum": 10}
                },
                "required": ["scene_id", "room_ids"]
            },
            default_config={"scene_id": "", "room_ids": [], "interval_seconds": 60.0},
            emitted_events=["LIVE_STARTED", "LIVE_ENDED"],
        ))
        self._poll_task: asyncio.Task | None = None
        self._live_state: dict[int, bool] = {}
        self._client = httpx.AsyncClient(timeout=10.0)

    async def on_load(self, context: PluginContext) -> None:
        self._context = context
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def on_unload(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._client.aclose()

    async def _poll_loop(self) -> None:
        while True:
            interval = float(self.manifest.config.get("interval_seconds", 60.0))
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Bilibili sensor poll failed: %s", e)
            await asyncio.sleep(max(10.0, interval))

    async def _poll_once(self) -> None:
        scene_id = self.manifest.config.get("scene_id", "")
        room_ids = self.manifest.config.get("room_ids", [])
        if not scene_id or not room_ids:
            return  # unconfigured sensor stays inert

        for room_id in room_ids:
            try:
                resp = await self._client.get(LIVE_API_URL, params={"room_id": int(room_id)})
                data = resp.json()
            except Exception as e:
                logger.warning("Bilibili API error for room %s: %s", room_id, e)
                continue

            if data.get("code") != 0:
                logger.warning("Bilibili API non-zero code for room %s: %s", room_id, data.get("code"))
                continue

            is_live = data.get("data", {}).get("live_status", 0) == 1
            title = str(data.get("data", {}).get("title", ""))[:80]
            was_live = self._live_state.get(int(room_id), False)

            if is_live and not was_live:
                await self._context.emit_event(Event(
                    event_type=EventType.LIVE_STARTED,
                    scene_id=scene_id,
                    actor_id=f"plugin:{self.manifest.id}",
                    payload={"raw_text": f"主播开播：{title}", "room_id": room_id, "title": title}
                ))
            elif not is_live and was_live:
                await self._context.emit_event(Event(
                    event_type=EventType.LIVE_ENDED,
                    scene_id=scene_id,
                    actor_id=f"plugin:{self.manifest.id}",
                    payload={"raw_text": f"主播下播：{title}", "room_id": room_id, "title": title}
                ))
            self._live_state[int(room_id)] = is_live
