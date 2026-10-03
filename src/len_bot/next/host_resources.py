"""Authenticated task-resource browsing, reading, download and explicit registration."""

from collections.abc import Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, JsonValue
from starlette.concurrency import run_in_threadpool

from .config_types import STRICT
from .host_materials import failure
from .host_task_models import RegisteredFile
from .network import NetworkRuntime
from .task_materials import MaterialName
from .task_resources import OpenedResource, PreviewKind, ResourceFileRef, ResourceLocation, ResourceScope, TaskResources
from .tasks_store import TaskStore


class RegisterResource(BaseModel):
    model_config = STRICT
    reference: ResourceFileRef
    requester: str = Field(pattern=r'^[1-9][0-9]*$')
    name: MaterialName
    note: str = ''


class ResourceEntry(BaseModel):
    name: str
    kind: str
    size: int | None
    modified: float
    purpose: str
    mime_type: str
    preview: PreviewKind
    exists: bool
    reference: ResourceFileRef
    note: str | None
    upload: dict[str, JsonValue] | None


class ResourceListing(ResourceLocation):
    scene: str
    exists: bool
    entries: list[ResourceEntry]
    next_offset: int | None


class ResourceText(BaseModel):
    text: str
    offset: int
    size: int
    next_offset: int | None
    encoding: str


class ResourceDownload(StreamingResponse):
    def __init__(self, opened: OpenedResource):
        self.source = opened.stream

        async def chunks():
            remaining = opened.size
            while remaining:
                chunk = await run_in_threadpool(self.source.read, min(65536, remaining))
                if not chunk:
                    raise OSError('Resource became shorter during download')
                remaining -= len(chunk)
                yield chunk

        super().__init__(chunks(), media_type=opened.mime_type, headers={
            'Content-Length': str(opened.size), 'Cache-Control': 'no-store',
            'Content-Disposition': f"attachment; filename*=utf-8''{quote(opened.name, safe='')}",
            'X-Content-Type-Options': 'nosniff',
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.source.close()


def register_host_resources(app: FastAPI, *, runtime: NetworkRuntime,
                            user: Callable[[Request], str]) -> None:
    resources = (None if runtime.config.worker is None else
                 TaskResources(runtime.config.worker, TaskStore(runtime.store)))

    def service(scene: str) -> TaskResources:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
        if resources is None:
            raise HTTPException(409, '当前未配置 worker，无法定位工作资源')
        return resources

    @app.get('/api/host/resources', response_model=ResourceListing)
    async def listing(scene: str, scope: ResourceScope, task_id: int | None = Query(None, gt=0), path: str = '',
                      offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), _: str = Depends(user)):
        try:
            location = ResourceLocation(scope=scope, task_id=task_id, path=path)
            return await service(scene).list(scene, location, offset=offset, limit=limit)
        except (ValueError, OSError) as error:
            raise failure(error) from error

    @app.get('/api/host/resources/text', response_model=ResourceText)
    async def text(scene: str, scope: ResourceScope, task_id: int | None = Query(None, gt=0), path: str = '',
                   file_id: int | None = Query(None, gt=0), offset: int = Query(0, ge=0),
                   limit: int = Query(20000, ge=1024, le=65536), _: str = Depends(user)):
        try:
            reference = ResourceFileRef(scope=scope, task_id=task_id, file_id=file_id, path=path)
            return await service(scene).read_text(scene, reference, offset=offset, limit=limit)
        except (ValueError, OSError) as error:
            raise failure(error) from error

    @app.get('/api/host/resources/content')
    async def content(scene: str, scope: ResourceScope, task_id: int | None = Query(None, gt=0), path: str = '',
                      file_id: int | None = Query(None, gt=0), preview: bool = False, _: str = Depends(user)):
        try:
            reference = ResourceFileRef(scope=scope, task_id=task_id, file_id=file_id, path=path)
            reader = service(scene)
            opened = reader.open(scene, reference)
            if preview and opened.size > reader.settings.max_file_bytes:
                opened.stream.close()
                raise ValueError('文件超过预览大小，使用原文件下载')
            return ResourceDownload(opened)
        except (ValueError, OSError) as error:
            raise failure(error) from error

    @app.post('/api/host/resources/register', response_model=RegisteredFile)
    async def register(scene: str, body: RegisterResource, _: str = Depends(user)):
        service(scene)
        if runtime.tasks is None:
            raise HTTPException(409, '当前没有任务服务')
        try:
            return await runtime.tasks.register_resource(scene, body.reference, requester=body.requester,
                                                        name=body.name, note=body.note)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError) as error:
            raise failure(error) from error
