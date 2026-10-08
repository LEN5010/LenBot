"""Authenticated notice, usage, log and diagnostic views of existing records."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import logging
import sqlite3
import os
import stat
from ...work.materials import finish_file_operation
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from ...runtime.logs import log_event, log_files, read_records
from ...work.storage import temporary_paths
from ...runtime.operations import diagnostic_zip
from ...work.store import TaskStore
from ...models.usage import usage
from ...models.limits import LimitReached, clear_speech, speech_quota
from ...models.client import ModelHTTPError, ModelProtocolError
from ...memory.jobs import processing_records


class ChatSwitch(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    enabled: bool


class QuietChange(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    seconds: int = Field(ge=1, le=604800)
    direct: Literal['allow', 'defer'] = 'allow'


def stderr_tail(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError(f'Task stderr is not a regular file: {path}')
        source.seek(0, os.SEEK_END)
        source.seek(max(0, source.tell() - 65536))
        return source.read(65536).decode('utf-8', 'replace')

logger = logging.getLogger(__name__)


def register_host_operations(app, *, runtime, user):
    config, store = runtime.config, runtime.store
    def scene_exists(scene):
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
    async def archive(payload):
        def plugin_redactor(text):
            if runtime.plugins is not None:
                for name in runtime.plugins.plugins:
                    text = runtime.plugins.redact(name, text)
            return text
        return Response(await finish_file_operation(diagnostic_zip, {"sampled_at": store.now(), **payload}, config,
                                                   plugin_redactor=plugin_redactor), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="diagnostic.zip"',
                                 'Cache-Control': 'no-store'})

    @app.post('/api/host/scenes/{scene}/history/{action}')
    async def history_operation(scene: str, action: Literal['compact', 'new-context'],
                                confirmed: bool = False, _: str = Depends(user)):
        scene_exists(scene)
        if not confirmed:
            raise HTTPException(422, '请明确确认本次会话操作')
        runner = runtime.runners[scene]
        if runner.execution.locked() or runtime.status in {'stopping', 'stopped'}:
            raise HTTPException(409, '场景正在执行或宿主正在停止，未改变会话')
        async with runner.execution:
            if store.db.execute('SELECT 1 FROM turns WHERE scene=? AND ended IS NULL LIMIT 1', (scene,)).fetchone():
                raise HTTPException(409, '场景还有未结束或等待恢复的轮次，未改变会话')
            if action == 'new-context':
                through = store.new_context(scene)
                runner.chat.toolset.discovered_tools = set()
                runtime.notify()
                return {'compact_through': through, 'message': '已开启新上下文；原记录、未读输入和后台工作保留'}
            try:
                return await runner.chat.compact_now()
            except (ValueError, TimeoutError, ModelHTTPError, ModelProtocolError) as error:
                raise HTTPException(422, f'{type(error).__name__}: {error}') from error

    @app.get('/api/host/scenes/{scene}/control')
    async def scene_control(scene: str, _: str = Depends(user)):
        scene_exists(scene)
        return runtime.runners[scene].control_state()

    @app.post('/api/host/scenes/{scene}/control/quiet')
    async def scene_quiet(scene: str, change: QuietChange, _: str = Depends(user)):
        scene_exists(scene)
        runner = runtime.runners[scene]
        if runner.execution.locked() or not runtime.accepting:
            raise HTTPException(409, '场景正在执行或宿主正在停止，临时状态未改变')
        async with runner.execution:
            return runner.set_temporary_quiet(change.seconds, change.direct, None)

    @app.post('/api/host/scenes/{scene}/control/resume')
    async def scene_resume(scene: str, _: str = Depends(user)):
        scene_exists(scene)
        runner = runtime.runners[scene]
        if runner.execution.locked() or not runtime.accepting:
            raise HTTPException(409, '场景正在执行或宿主正在停止，临时状态未改变')
        async with runner.execution:
            return runner.set_temporary_quiet(None, 'allow', None)

    @app.put('/api/host/scenes/{scene}/control/chat')
    async def scene_chat(scene: str, change: ChatSwitch, _: str = Depends(user)):
        scene_exists(scene)
        runner = runtime.runners[scene]
        if not runtime.accepting:
            raise HTTPException(409, '宿主没有在运行，聊天开关未改变')
        # Waits for a running turn to finish so its saved attention state cannot overwrite the switch.
        async with runner.execution:
            return runner.set_paused(not change.enabled)

    @app.get('/api/host/scenes/{scene}/notices')
    async def notices(scene: str, before: int | None = Query(None, ge=1),
                      limit: int = Query(50, ge=1, le=200), _: str = Depends(user)):
        scene_exists(scene)
        return store.notice_page(scene, before=before, limit=limit)

    @app.get('/api/host/scenes/{scene}/turns/{turn_id}/export')
    async def turn_export(scene: str, turn_id: str, _: str = Depends(user)):
        scene_exists(scene)
        detail = store.turn_detail(scene, turn_id)
        if detail is None:
            raise HTTPException(404, '当前场景没有这一轮')
        logs = await asyncio.to_thread(read_records, config.logging.directory, limit=2000, match={'turn_id': turn_id})
        return await archive({'turn': detail, 'log': list(reversed(logs)),
                        'scope': '实际轮次与调用，以及同一轮 ID 的运行日志；输入原话以保存的请求为准'})

    @app.get('/api/host/tasks/{task_id}/export')
    async def task_export(task_id: int, scene: str, _: str = Depends(user)):
        scene_exists(scene)
        tasks = TaskStore(store)
        try:
            task = tasks.get(scene, task_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        events = [dict(row) for row in store.db.execute(
            'SELECT * FROM task_events WHERE scene=? AND task_id=? ORDER BY id', (scene, task_id))]
        for event in events:
            event['body'] = json.loads(event['body'])
        logs = await asyncio.to_thread(read_records, config.logging.directory, limit=2000,
                                       match={'scene': scene, 'task_id': str(task_id)})
        stderr = {} if config.worker is None else {
            str(path.name): await asyncio.to_thread(stderr_tail, path)
            for path, kind in temporary_paths(config.worker, scene, task_id)
            if kind == 'file' and path.name.endswith('.stderr') and path.is_file()}
        return await archive({'task': asdict(task), 'events': events,
                        'files': [dict(row) for row in store.db.execute(
                            'SELECT name,size,note,created FROM task_files WHERE scene=? AND task_id=?',
                            (scene, task_id))],
                        'log': list(reversed(logs)), 'container_stderr_tail': stderr,
                        'scope': '已保存任务事件、文件清单、同一任务的运行日志和容器进程 stderr 末尾 64 KiB；不包含文件字节或原生会话文件'})

    @app.get('/api/host/retention')
    async def retention_state(_: str = Depends(user)):
        manager = runtime.retention
        return {'enabled': config.retention is not None, 'busy': manager.lock.locked(),
                'last_result': manager.last_result, 'error': manager.error,
                'preview': None if config.retention is None else await manager.batch_async(preview=True)}

    @app.post('/api/host/retention')
    async def retention_run(confirmed: bool = False, _: str = Depends(user)):
        if not confirmed:
            raise HTTPException(422, '清理不可撤销，请先预览并明确确认')
        manager = runtime.retention
        if manager.lock.locked():
            raise HTTPException(409, '已有清理批次正在执行')
        async with manager.lock:
            try:
                result = await manager.batch_async()
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            runtime.notify()
            return result

    def limit_state(scene, chat):
        quota = speech_quota(chat.store, chat.config)
        speech = {'source': quota.source, 'limit': quota.limit, 'used': quota.used, 'remaining': quota.remaining,
                  'reserve': quota.reserve, 'reserve_left': quota.reserve_left,
                  'held_until': quota.until(direct=False), 'blocked_until': quota.until(direct=True)}
        try:
            chat.check_limits(model=True, direct=True)
        except LimitReached as error:
            return {'scene': scene, 'blocked': True, 'reason': str(error), 'until': error.until, 'speech': speech}
        return {'scene': scene, 'blocked': False, 'reason': None, 'until': None, 'speech': speech}

    @app.get('/api/host/limits')
    async def limits_state(_: str = Depends(user)):
        return {'limits': config.limits.model_dump(mode='json'),
                'scenes': [limit_state(scene, chat) for scene, chat in runtime.chats.items()],
                'scope': '宿主代理的模型请求，含保留试聊及已移除场景；不含远端服务内部调用'}

    @app.post('/api/host/scenes/{scene}/speech/reset')
    async def speech_reset(scene: str, operator: str = Depends(user)):
        scene_exists(scene)
        chat = runtime.chats[scene]
        clear_speech(chat.store, scene)
        log_event(logger, 'speech_limit_reset', scene=scene, operator=operator)
        runtime.runners[scene].changed.set()
        runtime.notify()
        return limit_state(scene, chat)

    @app.get('/api/host/usage')
    async def costs(period: Literal['day', 'month'] = 'day', scene: str | None = None,
                    _: str = Depends(user)):
        if scene is not None:
            scene_exists(scene)
        zone = config.timezone if scene is None else config.scene_timezone(scene)
        now = datetime.fromtimestamp(store.now(), ZoneInfo(zone))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == 'month':
            start = start.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            end = start + timedelta(days=1)
        try:
            with processing_records(config.database, None if runtime.memory is None else runtime.memory.jobs) as records:
                result = usage(store, None if scene is None else [scene],
                               start.timestamp(), end.timestamp(), memory=runtime.memory,
                               memory_db=None if records is None else records.db)
                return {"timezone": zone, "period": period, **result,
                        'scope': '当前实例业务及记忆计量；全部范围含public与已移除场景，不含独立保留试聊（预算另含）',
                        'memory_record_source':
                        'not_present' if records is None else 'running_connection' if runtime.memory is not None else 'read_only_existing'}
        except (OSError, ValueError, sqlite3.Error) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    def log_paths():
        return log_files(config.logging.directory)

    @app.get('/api/host/log-files')
    async def files(_: str = Depends(user)):
        return {'enabled': True, 'timezone': 'UTC',
                'items': [{'name': p.name, 'bytes': p.stat().st_size} for p in log_paths()]}

    @app.get('/api/host/log-files/{name}')
    async def file(name: str, _: str = Depends(user)):
        paths = {p.name: p for p in log_paths()}
        if name not in paths:
            raise HTTPException(404, '没有这一份日志')
        # Diagnostic exports, unlike local files, also mask account-shaped numbers.
        return await archive({'log': await asyncio.to_thread(paths[name].read_text, encoding='utf-8')})
