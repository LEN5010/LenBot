"""Manage actual scene arrangements through the authenticated host panel."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ...runtime.network import NetworkRuntime
from ...chat.proactive import ProactiveStore
from ...chat.schedule import ScheduleArguments, cancel_arrangement, create_arrangement
from ...chat.schedule_store import ScheduleStore


class PanelScheduleArguments(ScheduleArguments):
    requester: str = Field(pattern=r"^[a-z][a-z0-9_-]*:[^:\s/\\]+$")


class CancelArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requester: str = Field(pattern=r"^[a-z][a-z0-9_-]*:[^:\s/\\]+$")


def register_host_schedules(app: FastAPI, *, runtime: NetworkRuntime,
                            user: Callable[[Request], str]) -> None:
    def chat_for(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return runtime.chats[scene]

    def changed(scene: str) -> None:
        runtime.runners[scene].changed.set()
        runtime.notify()

    @app.get("/api/host/schedules/state")
    async def state(_: str = Depends(user)):
        return {"timezone": runtime.config.timezone,
                "scenes": [{"scene": scene, "timezone": chat.config.timezone,
                            **chat.config.schedules.model_dump(mode="json"),
                            "tool_allowed": "schedule" in chat.toolset.allowed_tool_names}
                           for scene, chat in runtime.chats.items()]}

    @app.get("/api/host/schedules/proactive")
    async def proactive(scene: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20),
                        _: str = Depends(user)):
        chat = chat_for(scene)
        settings = chat.config.proactive
        records = ProactiveStore(runtime.store)
        now = runtime.store.now()
        exclude = tuple(chat.config.attention.other_bot_ids)
        pause = records.pause(scene)
        items = records.page(scene, limit=limit + 1, offset=offset)
        next_at, next_reason = (None, None) if settings is None else records.next_at(
            scene, settings, chat.config.timezone, chat.config.attention.quiet_hours, now, exclude)
        return {"scene": scene, "timezone": chat.config.timezone, "now": now,
                "settings": None if settings is None else settings.model_dump(mode="json"),
                "idle_since": records.last_activity(scene, exclude),
                "pause": pause if pause is not None and pause["until"] > now else None,
                "next_at": next_at, "next_reason": next_reason,
                "items": items[:limit], "next_offset": offset + limit if len(items) > limit else None}

    @app.get("/api/host/schedules")
    async def listing(scene: str,
                      status: Literal["active", "all", "pending", "blocked", "delivered", "cancelled"] = "active",
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20),
                      _: str = Depends(user)):
        chat_for(scene)
        items = ScheduleStore(runtime.store).list_schedules(scene, status=status, offset=offset, limit=limit + 1)
        return {"items": [asdict(item) for item in items[:limit]],
                "next_offset": offset + limit if len(items) > limit else None}

    @app.post("/api/host/schedules")
    async def create(scene: str, body: PanelScheduleArguments, _: str = Depends(user)):
        chat = chat_for(scene)
        if not runtime.accepting:
            raise HTTPException(409, "宿主正在停止，不再创建新安排")
        if "schedule" not in chat.toolset.allowed_tool_names:
            raise HTTPException(403, "当前角色或场景未开放 schedule")
        try:
            item = create_arrangement(runtime.store, chat.config, body, now=runtime.store.now)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        changed(scene)
        return asdict(item)

    @app.post("/api/host/schedules/{id}/cancel")
    async def cancel(id: int, scene: str, body: CancelArguments, _: str = Depends(user)):
        chat = chat_for(scene)
        try:
            ScheduleStore(runtime.store).get_schedule(scene, id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        try:
            item = cancel_arrangement(runtime.store, chat.config, id=id, requester=body.requester)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        changed(scene)
        return asdict(item)
