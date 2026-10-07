"""Authenticated task-resource browsing, reading, download and explicit registration."""

from collections.abc import Callable
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, JsonValue
from starlette.concurrency import run_in_threadpool

from ...configuration.types import STRICT
from .materials import failure
from .owner import panel_owner
from ..task_models import RegisteredFile
from ...runtime.network import NetworkRuntime
from ...work.materials import MaterialName
from ...work.resources import OpenedResource, PreviewKind, ResourceFileRef, ResourceLocation, ResourceScope, TaskResources
from ...work.store import TaskStore


class RegisterResource(BaseModel):
    model_config = STRICT
    reference: ResourceFileRef
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
    source: dict[str, JsonValue] | None
    browser_source: dict[str, JsonValue] | None
    registrations: list[int]
    deletion: dict[str, JsonValue] | None
    deletable: bool


class AdoptResource(BaseModel):
    model_config = STRICT
    reference: ResourceFileRef
    name: MaterialName


class DeleteResource(BaseModel):
    model_config = STRICT
    reference: ResourceFileRef


class UploadResource(BaseModel):
    model_config = STRICT
    name: MaterialName
    file: UploadFile


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
            return await runtime.tasks.register_resource(scene, body.reference, requester=panel_owner(runtime),
                                                        name=body.name, note=body.note)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError) as error:
            raise failure(error) from error

    def tasks(scene: str):
        service(scene)
        if runtime.tasks is None:
            raise HTTPException(409, '当前没有任务服务')
        return runtime.tasks

    @app.post('/api/host/resources/adopt')
    async def adopt(scene: str, body: AdoptResource, _: str = Depends(user)):
        try:
            return await tasks(scene).adopt_resource(scene, body.reference, requester=panel_owner(runtime), name=body.name)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError, RuntimeError) as error:
            raise failure(error) from error

    @app.delete('/api/host/resources')
    async def delete(scene: str, body: DeleteResource, _: str = Depends(user)):
        try:
            return await tasks(scene).delete_resource(scene, body.reference, requester=panel_owner(runtime))
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError) as error:
            raise failure(error) from error

    @app.post('/api/host/resources/upload')
    async def upload(scene: str, body: Annotated[UploadResource, Form(media_type='multipart/form-data')],
                     _: str = Depends(user)):
        try:
            return await tasks(scene).upload_resource(scene, body.file.file, requester=panel_owner(runtime), name=body.name)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, OSError, RuntimeError) as error:
            raise failure(error) from error
        finally:
            await body.file.close()
