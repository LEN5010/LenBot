"""Authenticated access to actual tasks, their native records and copied files."""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
import os
from pathlib import Path
import stat

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .network import NetworkRuntime
from .tasks import file_info
from .tasks_store import TERMINAL, TaskStore
from .tasks_tools import DelegateArguments, TaskArguments, perform_task_action


class _SessionDownload(StreamingResponse):
    """Own one opened native file for the whole HTTP response, including disconnect."""

    def __init__(self, source, size: int, task_id: int):
        self.source = source

        async def chunks():
            remaining = size
            while remaining:
                chunk = await run_in_threadpool(source.read, min(65536, remaining))
                if not chunk:
                    raise OSError("Pi session file became shorter during download")
                remaining -= len(chunk)
                yield chunk

        super().__init__(chunks(), media_type="application/x-ndjson", headers={
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="task-{task_id}-session.jsonl"',
            "Cache-Control": "no-store",
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.source.close()


def register_host_tasks(app: FastAPI, *, runtime: NetworkRuntime,
                        user: Callable[[Request], str], host_changes: set[asyncio.Event]) -> None:
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
                "scenes": [{"scene": scene, "timezone": chat.config.timezone, **chat.config.tasks.model_dump(mode="json"),
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
            return {"task": {**asdict(item), "active_timeout_seconds": None if runtime.tasks is None else runtime.tasks.active_timeout(item)}, "events": events,
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

    @app.get("/api/host/tasks/{id}/session")
    async def session(id: int, scene: str, _: str = Depends(user)):
        scene_exists(scene)
        try:
            item = records.get(scene, id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        if item.status not in TERMINAL or item.container is not None:
            raise HTTPException(409, "会话下载只读取已结束且容器已停止的任务")
        worker = runtime.config.worker
        if worker is None:
            raise HTTPException(409, "当前根配置没有 worker，无法定位原任务工作区")
        path = worker.workspace_root.resolve() / scene / "tasks" / str(id) / "session.jsonl"
        try:
            if path.resolve(strict=True) != path:
                raise HTTPException(409, "任务会话路径含符号链接，不是本任务的原生文件")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError as error:
            raise HTTPException(404, "本任务尚无 session.jsonl；启动失败或尚未写入首条消息时可能没有文件") from error
        except OSError as error:
            raise HTTPException(409, f"读取任务会话失败：{error}") from error
        source = os.fdopen(descriptor, "rb")
        try:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise HTTPException(409, "任务会话不是普通文件")
            return _SessionDownload(source, info.st_size, id)
        except BaseException:
            source.close()
            raise

    @app.websocket("/api/host/tasks/{id}/live")
    async def live(websocket: WebSocket, id: int, scene: str):
        service = runtime.tasks
        try:
            user(websocket)
            scene_exists(scene)
            records.get(scene, id)
            if service is None:
                raise HTTPException(409, "当前宿主未配置任务执行器")
        except (HTTPException, ValueError) as error:
            reason = error.detail if isinstance(error, HTTPException) else str(error)
            await websocket.close(code=1008, reason=str(reason))
            return
        await websocket.accept()
        changed = asyncio.Event()
        listeners = service.live_listeners.setdefault(id, set())
        listeners.add(changed)
        # Host changes include logout, so an idle task stream loses access too.
        host_changes.add(changed)
        changed.set()

        async def push():
            while True:
                await changed.wait()
                changed.clear()
                user(websocket)
                await websocket.send_json(service.live_snapshot(scene, id))
                # Coalesce deltas; slow clients receive the latest projection,
                # not a token queue or a database refresh for each delta.
                await asyncio.sleep(0.25)

        pushing = asyncio.create_task(push())
        receiving = asyncio.create_task(websocket.receive())
        try:
            done, _ = await asyncio.wait({pushing, receiving}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = await task
                if task is receiving and result["type"] != "websocket.disconnect":
                    await websocket.close(code=1003, reason="This stream only reports task text")
        except HTTPException:
            await websocket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            listeners.remove(changed)
            if not listeners:
                del service.live_listeners[id]
            host_changes.remove(changed)
            pushing.cancel()
            receiving.cancel()
            await asyncio.gather(pushing, receiving, return_exceptions=True)

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
