"""Management views of one already constructed multi-scene network runtime."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket

from len_bot.web.shell import mount_panel
from .config import HostConfig
from .host_capabilities import register_host_capabilities
from .host_persona import register_host_persona
from .host_persona_stickers import register_host_persona_stickers
from .host_persona_avatar import register_host_persona_avatar
from .host_plugins import register_host_plugins
from .host_mcp import register_host_mcp
from .host_audio import register_host_audio
from .host_trials import HostTrials, register_host_trials
from .host_browser import register_host_browser
from .host_permissions import register_host_permissions
from .host_settings import register_host_settings
from .host_operations import register_host_operations
from .host_memory import register_host_memory
from .host_tasks import register_host_tasks
from .host_materials import register_host_materials
from .host_skills import register_host_skills
from .host_schedules import register_host_schedules
from .host_learning import register_host_learning
from .host_jargon import register_host_jargon
from .host_stickers import register_host_stickers
from .host_reply_effects import register_host_reply_effects
from .network import NetworkRuntime
from .panel_auth import changes_socket, install_panel_auth


def create_app(config: HostConfig, runtime: NetworkRuntime, *, root: Path) -> FastAPI:
    if config.panel is None:
        raise ValueError("Multi-scene host panel requires panel configuration in lenbot.config.json")

    runtime.budget.trials_root = root / ".runtime" / "chat-tests"
    write_lock = asyncio.Lock()
    trials = HostTrials(config, runtime, root, write_lock=write_lock)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await trials.close()

    app = FastAPI(title="LenBot 运行管理", lifespan=lifespan)
    app.state.trials = trials
    listeners: set[asyncio.Event] = set()

    def notify() -> None:
        for changed in listeners:
            changed.set()

    runtime.on_update = notify
    def logged_out() -> None:
        notify()
        trials.notify()

    user = install_panel_auth(app, config.panel, on_logout=logged_out)
    register_host_operations(app, runtime=runtime, user=user)
    register_host_trials(app, trials, user)
    register_host_browser(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_permissions(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_capabilities(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_settings(app, root=root, running=config, user=user, write_lock=write_lock,
                           personas=lambda: {scene: chat.persona for scene, chat in runtime.chats.items()})
    register_host_persona(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_persona_stickers(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_persona_avatar(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_memory(app, runtime=runtime, user=user)
    register_host_tasks(app, runtime=runtime, user=user, host_changes=listeners, write_lock=write_lock)
    register_host_materials(app, runtime=runtime, user=user)
    register_host_skills(app, root=root, runtime=runtime, user=user, write_lock=write_lock)
    register_host_schedules(app, runtime=runtime, user=user)
    register_host_learning(app, runtime=runtime, user=user)
    register_host_jargon(app, runtime=runtime, user=user)
    register_host_stickers(app, runtime=runtime, user=user)
    register_host_reply_effects(app, runtime=runtime, user=user)
    register_host_plugins(app, root=root, runtime=runtime, running=config, user=user, write_lock=write_lock)
    register_host_mcp(app, root=root, runtime=runtime, running=config, user=user, write_lock=write_lock)
    register_host_audio(app, runtime=runtime, user=user)

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
                "last_error": runtime.last_runtime_error or runtime.last_platform_error,
                "can_connect": runtime.can_connect,
                "accepting": runtime.accepting,
            },
            "scenes": [
                {"scene": scene, "persona": {"id": runtime.chats[scene].persona.id,
                                              "name": runtime.chats[scene].persona.name},
                 "persona_path": str(settings.persona),
                 "voice_mode": settings.voice_mode, "timezone": config.scene_timezone(scene)}
                for scene, settings in config.scenes.items()
            ],
            "memory_backend": None if config.memory is None else config.memory.backend,
            # Only what needs attention on the home page; details live on the capabilities page.
            "plugins": None if runtime.plugins is None else [
                {"name": item["name"], "status": item["status"], "error": item["error"],
                 "error_count": len(item["errors"]),
                 "latest_error": item["errors"][0] if item["errors"] else None}
                for item in runtime.plugins.state()["plugins"]
            ],
            "mcp": [] if runtime.mcp is None else [
                {"name": item["name"], "status": item["status"], "error": item["error"],
                 "error_count": len(item["errors"]),
                 "latest_error": item["errors"][0] if item["errors"] else None} for item in runtime.mcp.state()
            ],
        }

    @app.post("/api/host/connection/connect", status_code=202)
    async def connect(_: str = Depends(user)):
        try:
            runtime.request_connection()
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        return {"status": runtime.status}

    @app.get("/api/host/logs")
    async def logs(_: str = Depends(user)):
        return {"items": list(reversed(runtime.logs)), "capacity": 500,
                "scope": "本次进程宿主终端事件；不含第三方日志，重启清空"}

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
            "timezone": config.scene_timezone(scene),
            "bot_qq": config.bot_qq,
            "persona": {"id": chat.persona.id, "name": chat.persona.name},
            "delivery": config.delivery,
            "voice_mode": config.scenes[scene].voice_mode,
            "models": {"mind": config.models.roles.mind.model,
                       "voice": config.models.roles.voice.model},
            "messages": [
                {"seq": seq, "rendered": chat.context.render(message), "text": chat.context.render_text(message), **asdict(message),
                 "images": runtime.store.message_media(scene, seq)}
                for seq, message in runtime.store.recent_records(scene)
            ],
            "turns": runtime.store.recent_turns(scene),
        }

    @app.get("/api/host/scenes/{scene}/messages/{seq}/images/{image_index}")
    async def original_image(scene: str, seq: int, image_index: int, _: str = Depends(user)):
        configured_scene(scene)
        image = runtime.store.original_image(scene, seq, image_index)
        if image is None:
            raise HTTPException(404, "当前场景没有这张已保存图片",
                                headers={"Cache-Control": "no-store"})
        mime_type, data = image
        return Response(data, media_type=mime_type, headers={"Cache-Control": "no-store"})

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
        return {"scene": scene, "timezone": config.scene_timezone(scene), "active_only": active_only,
                **runtime.store.mind_history_page(scene, before=before, limit=limit,
                                                 active_only=active_only)}

    @app.websocket("/api/host/events")
    async def events(websocket: WebSocket):
        await changes_socket(websocket, listeners)

    mount_panel(app, mode="isolated-multi", home="/host/overview", assets_dir=config.panel.assets_dir)
    return app
