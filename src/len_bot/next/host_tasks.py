"""Authenticated access to actual tasks, their native records and copied files."""

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .network import NetworkRuntime
from .tasks import file_info
from .tasks_store import TaskStore
from .tasks_tools import DelegateArguments, TaskArguments, perform_task_action


def register_host_tasks(app: FastAPI, *, runtime: NetworkRuntime,
                        user: Callable[[Request], str]) -> None:
    records = TaskStore(runtime.store)

    def scene_exists(scene: str) -> None:
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")

    @app.get("/api/host/tasks/state")
    async def state(_: str = Depends(user)):
        service = runtime.tasks
        return {"configured": service is not None,
                "timezone": runtime.config.timezone,
                "accepting": service is not None and service.accepting,
                "error": None if service is None else service.error,
                "scenes": [{"scene": scene, **chat.config.tasks.model_dump(mode="json"),
                            "network_today": None if service is None else service.egress.status(scene)}
                           for scene, chat in runtime.chats.items()],
                "public_network": service is not None and service.settings.egress.enabled,
                "file_upload": (runtime.config.delivery == "onebot" and service is not None
                                and runtime.config.onebot.upload_visible_root is not None),
                "notice": "模型与公共流量使用独立管道；是否启用与目标是否连通分开。文件登记与上传回执分别显示。"}

    @app.get("/api/host/tasks")
    async def listing(scene: str, status: str = "active", offset: int = Query(0, ge=0),
                      limit: int = Query(20, ge=1, le=20), _: str = Depends(user)):
        scene_exists(scene)
        try:
            items = records.list(scene, status=status, offset=offset, limit=limit + 1)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return {"items": [asdict(item) for item in items[:limit]],
                "next_offset": offset + limit if len(items) > limit else None}

    @app.get("/api/host/tasks/{id}")
    async def detail(id: int, scene: str, after: int = Query(0, ge=0),
                     limit: int = Query(100, ge=1, le=100), _: str = Depends(user)):
        scene_exists(scene)
        try:
            item = records.get(scene, id)
            events = records.event_previews(scene, id, after=after, limit=limit)
            return {"task": asdict(item), "events": events,
                    "network": None if runtime.tasks is None else runtime.tasks.egress.status(scene, id),
                    "next_after": events[-1]["id"] if events else after,
                    "files": [file_info(file, records) for file in records.list_files(scene, id)]}
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/host/tasks/{id}/events/{event_id}")
    async def event(id: int, event_id: int, scene: str, _: str = Depends(user)):
        scene_exists(scene)
        try:
            return records.event(scene, id, event_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.get("/api/host/tasks/{id}/files/{file_id}")
    async def download(id: int, file_id: int, scene: str, _: str = Depends(user)):
        scene_exists(scene)
        try:
            file = records.get_file(scene, id, file_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        if not Path(file.path).is_file():
            raise HTTPException(404, "已登记的交付副本在磁盘上不存在")
        return FileResponse(file.path, filename=file.name)

    @app.post("/api/host/tasks/delegate")
    async def delegate(scene: str, body: DelegateArguments, _: str = Depends(user)):
        scene_exists(scene)
        if runtime.tasks is None:
            raise HTTPException(409, "尚未配置任务执行器")
        try:
            return await runtime.tasks.delegate(scene, **body.model_dump())
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/host/tasks/action")
    async def action(scene: str, body: TaskArguments, _: str = Depends(user)):
        scene_exists(scene)
        if runtime.tasks is None:
            raise HTTPException(409, "尚未配置任务执行器")
        try:
            return await perform_task_action(runtime.tasks, scene, body)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
