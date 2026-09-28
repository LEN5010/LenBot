"""Management views of one already constructed multi-scene network runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket

from len_bot.web.shell import mount_panel
from .config import HostConfig
from .host_capabilities import register_host_capabilities
from .host_persona import register_host_persona
from .host_settings import register_host_settings
from .host_memory import register_host_memory
from .host_tasks import register_host_tasks
from .network import NetworkRuntime
from .panel_auth import changes_socket, install_panel_auth


def create_app(config: HostConfig, runtime: NetworkRuntime, *, root: Path) -> FastAPI:
    if config.panel is None:
        raise ValueError("Multi-scene host panel requires panel configuration in lenbot.config.json")

    app = FastAPI(title="LenBot 运行管理")
    listeners: set[asyncio.Event] = set()

    def notify() -> None:
        for changed in listeners:
            changed.set()

    runtime.on_update = notify
    user = install_panel_auth(app, config.panel, on_logout=notify)
    write_lock = asyncio.Lock()
    register_host_capabilities(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_settings(app, root=root, running=config, user=user,
                           write_lock=write_lock)
    register_host_persona(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_memory(app, runtime=runtime, user=user)
    register_host_tasks(app, runtime=runtime, user=user, host_changes=listeners)

    def configured_scene(scene: str) -> None:
        if scene not in config.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")

    @app.get("/api/host/state")
    async def state(_: str = Depends(user)):
        return {
            "bot_qq": config.bot_qq,
            "timezone": config.timezone,
            "delivery": config.delivery,
            "connection": {
                "mode": config.onebot.mode,
                "connected": runtime.platform.connected,
                "status": runtime.status,
                "addresses": runtime.platform.addresses,
                "last_error": runtime.last_platform_error,
            },
            "scenes": [
                {"scene": scene, "persona": {"id": runtime.chats[scene].persona.id,
                                              "name": runtime.chats[scene].persona.name},
                 "voice_mode": settings.voice_mode}
                for scene, settings in config.scenes.items()
            ],
        }

    @app.get("/api/host/overview")
    async def overview(_: str = Depends(user)):
        now = datetime.now(ZoneInfo(config.timezone))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return {"timezone": config.timezone, "sampled_at": now.timestamp(),
                "since": start.timestamp(), "until": end.timestamp(),
                **runtime.store.daily_overview(list(config.scenes), start.timestamp(), end.timestamp())}

    @app.get("/api/host/scenes/{scene}")
    async def scene_state(scene: str, _: str = Depends(user)):
        configured_scene(scene)
        chat = runtime.chats[scene]
        return {
            "scene": scene,
            "timezone": config.timezone,
            "bot_qq": config.bot_qq,
            "persona": {"id": chat.persona.id, "name": chat.persona.name},
            "delivery": config.delivery,
            "voice_mode": config.scenes[scene].voice_mode,
            "models": {"mind": config.models.roles.mind.model,
                       "voice": config.models.roles.voice.model},
            "messages": [
                {"seq": seq, "rendered": chat.render(message), **asdict(message)}
                for seq, message in runtime.store.recent_records(scene)
            ],
            "turns": runtime.store.recent_turns(scene),
        }

    @app.get("/api/host/scenes/{scene}/turns/{turn_id}")
    async def turn(scene: str, turn_id: str, _: str = Depends(user)):
        configured_scene(scene)
        result = runtime.store.turn_detail(scene, turn_id)
        if result is None:
            raise HTTPException(404, "当前场景没有这一轮")
        return result

    @app.get("/api/host/scenes/{scene}/history")
    async def history(scene: str, before: int | None = Query(default=None, ge=1),
                      limit: int = Query(default=50, ge=1, le=200), active_only: bool = True,
                      _: str = Depends(user)):
        configured_scene(scene)
        return {"scene": scene, "timezone": config.timezone, "active_only": active_only,
                **runtime.store.mind_history_page(scene, before=before, limit=limit,
                                                 active_only=active_only)}

    @app.websocket("/api/host/events")
    async def events(websocket: WebSocket):
        await changes_socket(websocket, listeners)

    mount_panel(app, mode="isolated-multi", home="/host", assets_dir=config.panel.assets_dir)
    return app
