"""Task-space summaries and explicit per-task or batch file cleanup."""

from collections.abc import Callable
from dataclasses import asdict
import time
import traceback
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from .config_types import STRICT
from .host_materials import failure
from .network import NetworkRuntime
from .task_materials import finish_file_operation
from .task_storage import continuation_state, storage_usage, temporary_usage
from .storage_pool import worker_pool_usage
from .tasks_config import WorkerSettings
from .tasks_store import TERMINAL, Task, TaskStore


class CleanupSelection(BaseModel):
    model_config = STRICT
    task_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=100)
    requester: str = Field(pattern=r'^[1-9][0-9]*$')
    operation: Literal['temporary', 'environment']

    @model_validator(mode='after')
    def distinct_tasks(self):
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError('一次清理中的任务 ID 必须不同')
        return self


class CloseEnvironment(BaseModel):
    model_config = STRICT
    requester: str = Field(pattern=r'^[1-9][0-9]*$')


def register_host_task_storage(app: FastAPI, *, runtime: NetworkRuntime,
                               user: Callable[[Request], str]) -> None:
    records = TaskStore(runtime.store)

    def worker_for(scene: str) -> WorkerSettings:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
        if runtime.config.worker is None:
            raise HTTPException(409, '当前未配置任务文件目录')
        return runtime.config.worker

    def cleanable(item: Task) -> bool:
        return (runtime.tasks is not None and item.status in TERMINAL and item.container is None
                and not item.browser_active and item.id not in runtime.tasks.running)

    @app.get('/api/host/task-storage/pool')
    async def pool(_: str = Depends(user)):
        worker = runtime.config.worker
        if worker is None or worker.storage_pool is None:
            return {'configured': False, 'usage': None}
        try:
            usage = await finish_file_operation(worker_pool_usage, worker)
        except (ValueError, OSError, RuntimeError) as error:
            raise HTTPException(409, ''.join(traceback.format_exception_only(error)).strip()) from error
        return {'configured': True, 'usage': usage}

    @app.get('/api/host/task-storage')
    async def listing(scene: str, status: Literal['all', 'active', 'terminal', 'done', 'failed', 'cancelled'] = 'all',
                      since: float | None = Query(None, ge=0), before: float | None = Query(None, ge=0),
                      environment: Literal['all', 'retained', 'discarded'] = 'all', measure: bool = False,
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=20), _: str = Depends(user)):
        worker = worker_for(scene)
        if since is not None and before is not None and since >= before:
            raise HTTPException(422, '开始时间须早于结束时间')
        tasks = records.list(scene, status=status, since=since, before=before, environment=environment,
                             offset=offset, limit=limit + 1)
        items = []
        for item in tasks[:limit]:
            roots, error = None, None
            if measure:
                try:
                    roots = await finish_file_operation(storage_usage, worker, scene, item.id)
                except (ValueError, OSError) as problem:
                    error = ''.join(traceback.format_exception_only(problem)).strip()
            current = records.get(scene, item.id)
            discarded = records.workspace_discarded(scene, item.id)
            items.append({'id': item.id, 'goal': item.goal, 'requester': item.requester, 'status': current.status,
                'created': item.created, 'ended': current.ended,
                'continuation': continuation_state(current, discarded), 'cleanable': cleanable(current),
                'roots': roots, 'usage_error': error})
        return {'scene': scene, 'items': items, 'measured': measure,
                'next_offset': offset + limit if len(tasks) > limit else None}

    @app.get('/api/host/tasks/{id}/storage')
    async def storage(id: int, scene: str, _: str = Depends(user)):
        worker = worker_for(scene)
        started = time.time()
        try:
            before = records.get(scene, id)
            roots = await finish_file_operation(storage_usage, worker, scene, id)
            temporary = await finish_file_operation(temporary_usage, worker, scene, id)
            quota = await finish_file_operation(worker_pool_usage, worker)
            current = records.get(scene, id)
        except (ValueError, OSError) as error:
            raise failure(error) from error
        except RuntimeError as error:
            raise HTTPException(409, str(error)) from error
        discarded = records.workspace_discarded(scene, id)
        return {'scene': scene, 'task_id': id, 'started_at': started, 'ended_at': time.time(),
                'status_at_start': before.status, 'status_at_end': current.status,
                'container_at_start': before.container, 'container_at_end': current.container,
                'task': asdict(current), 'continuation': continuation_state(current, discarded),
                'cleanable': cleanable(current), 'roots': roots, 'temporary': temporary,
                'last_cleanup': records.latest_cleanup(scene, id),
                'hard_disk_quota': quota,
                'notice': '显示本次读取的文件逻辑大小与文件系统分配字节；运行中目录可继续变化。配额与剩余空间在实例存储池卡片读取。'}

    @app.post('/api/host/task-storage/cleanup')
    async def cleanup(scene: str, body: CleanupSelection, _: str = Depends(user)):
        worker_for(scene)
        if runtime.tasks is None:
            raise HTTPException(409, '当前没有任务服务')
        results = []
        for id in body.task_ids:
            try:
                result = await runtime.tasks.clean_task_files(scene, id, requester=body.requester, operation=body.operation)
            except (ValueError, RuntimeError, OSError) as error:
                results.append({'task_id': id, 'status': 'error', 'error': ''.join(traceback.format_exception_only(error)).strip(),
                                'removal': None})
            else:
                results.append({'task_id': id, 'status': 'complete', 'error': None, 'removal': result})
        return {'scene': scene, 'operation': body.operation, 'items': results}

    @app.post('/api/host/tasks/{id}/close-environment')
    async def close_environment(id: int, scene: str, body: CloseEnvironment, _: str = Depends(user)):
        worker_for(scene)
        if runtime.tasks is None:
            raise HTTPException(409, '当前没有任务服务')
        try:
            return await runtime.tasks.close_task_environment(scene, id, requester=body.requester)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from error
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, ''.join(traceback.format_exception_only(error)).strip()) from error
        except OSError as error:
            raise failure(error) from error
