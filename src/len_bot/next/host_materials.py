"""Authenticated, explicit scene-material copies and downloads, never automatic task inputs."""

import asyncio
from collections.abc import Callable
import traceback
from typing import BinaryIO
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config_types import STRICT
from .network import NetworkRuntime
from .task_materials import MaterialName, adopt_file, finish_file_operation, list_materials, material_directory, open_regular
from .tasks_store import TaskStore
from .tasks_config import WorkerSettings


class MaterialAdoption(BaseModel):
    model_config = STRICT
    task_id: int = Field(gt=0, strict=True)
    file_id: int = Field(gt=0, strict=True)
    directory: str = Field(min_length=1)
    name: MaterialName
    confirmed: bool

class MaterialDownload(StreamingResponse):
    def __init__(self, source: BinaryIO, size: int, name: str):
        self.source = source

        async def chunks():
            remaining = size
            while remaining:
                chunk = await run_in_threadpool(source.read, min(65536, remaining))
                if not chunk:
                    raise OSError('Shared material became shorter during download')
                remaining -= len(chunk)
                yield chunk

        super().__init__(chunks(), media_type='application/octet-stream', headers={
            'Content-Length': str(size), 'Content-Disposition': f"attachment; filename*=utf-8''{quote(name, safe='')}",
            'Cache-Control': 'no-store',
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.source.close()


def failure(error: ValueError | OSError) -> HTTPException:
    status = 404 if isinstance(error, FileNotFoundError) else 409 if isinstance(error, (ValueError, FileExistsError)) else 500
    return HTTPException(status, ''.join(traceback.format_exception_only(error)).strip())


def register_host_materials(app: FastAPI, *, runtime: NetworkRuntime, user: Callable[[Request], str]) -> None:
    records = TaskStore(runtime.store)
    changes = asyncio.Lock()

    def worker_for(scene: str) -> WorkerSettings:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
        if runtime.config.worker is None:
            raise HTTPException(409, '当前运行配置没有 worker，无法定位共享资料和交付原件')
        return runtime.config.worker

    @app.get('/api/host/materials')
    async def listing(scene: str, request: Request, _: str = Depends(user)):
        worker = worker_for(scene)
        async with changes:
            try:
                result = await finish_file_operation(list_materials, material_directory(worker.workspace_root, scene))
                user(request)
                return {**result, 'scene': scene,
                        'notice': '当前运行配置的本场景普通共享文件；不是共享技能或公共资料。'
                                  '当前只保全/下载，未自动挂入任务、读取内容或上传平台。'}
            except (ValueError, OSError) as error:
                raise failure(error) from error

    @app.post('/api/host/materials/adopt')
    async def adoption(scene: str, body: MaterialAdoption, request: Request, _: str = Depends(user)):
        worker = worker_for(scene)
        if not body.confirmed:
            raise HTTPException(422, '先明确确认已登记副本适合保存在本场景共享目录')
        directory = material_directory(worker.workspace_root, scene)
        if body.directory != str(directory):
            raise HTTPException(409, f'当前共享目录与页面读取不同，未采用：expected={str(directory)!r}, received={body.directory!r}')
        try:
            source = records.get_file(scene, body.task_id, body.file_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        async with changes:
            try:
                result = await finish_file_operation(adopt_file, directory, source,
                    delivery_root=worker.delivery_root, name=body.name, max_bytes=worker.max_file_bytes)
            except (ValueError, OSError) as error:
                raise failure(error) from error
            user(request)
            return {**result, 'scene': scene, 'notice': '共享原件已复制，源交付副本保留；未挂入任务或上传平台。'}

    @app.get('/api/host/materials/file')
    async def download(scene: str, name: MaterialName = Query(), _: str = Depends(user)):
        worker = worker_for(scene)
        try:
            directory = material_directory(worker.workspace_root, scene)
            source, size = open_regular(directory / name)
        except (ValueError, OSError) as error:
            raise failure(error) from error
        try:
            return MaterialDownload(source, size, name)
        except BaseException:
            source.close()
            raise
