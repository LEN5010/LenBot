"""Authenticated notice, usage, log and diagnostic views of existing records."""
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import re
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Response

from .operations import diagnostic_zip
from .tasks_store import TaskStore
from .usage import usage
from .limits import LimitReached


def register_host_operations(app, *, runtime, user):
    config, store = runtime.config, runtime.store
    def scene_exists(scene):
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
    def archive(payload):
        def plugin_redactor(text):
            if runtime.plugins is not None:
                for name in runtime.plugins.plugins:
                    text = runtime.plugins.redact(name, text)
            return text
        return Response(diagnostic_zip({"sampled_at": store.now(), **payload}, config,
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
                runner.chat.discovered_tools = set()
                runtime.notify()
                return {'compact_through': through, 'message': '已开启新上下文；原记录、未读输入和后台工作保留'}
            try:
                return await runner.chat.compact_now()
            except (ValueError, TimeoutError) as error:
                raise HTTPException(422, f'{type(error).__name__}: {error}') from error

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
        return archive({'turn': detail, 'scope': '实际轮次与调用；输入原话以保存的请求为准，不猜时间窗口关联'})

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
        return archive({'task': asdict(task), 'events': events,
                        'files': [dict(row) for row in store.db.execute(
                            'SELECT name,size,note,created FROM task_files WHERE scene=? AND task_id=?',
                            (scene, task_id))], 'scope': '已保存任务事件与文件清单；不包含文件字节或原生会话文件'})

    @app.get('/api/host/limits')
    async def limits_state(_: str = Depends(user)):
        items = []
        for scene, chat in runtime.chats.items():
            try:
                chat.check_limits(model=True)
            except LimitReached as error:
                items.append({'scene': scene, 'blocked': True, 'reason': str(error), 'until': error.until})
            else:
                items.append({'scene': scene, 'blocked': False, 'reason': None, 'until': None})
        return {'limits': config.limits.model_dump(mode='json'), 'scenes': items,
                'scope': '宿主代理的模型请求，含保留试聊及已移除场景；不含远端服务内部调用'}

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
        return {"timezone": zone, "period": period, **usage(store, list(runtime.chats) if scene is None else [scene],
                start.timestamp(), end.timestamp(), memory=runtime.memory)}

    def log_files():
        if config.logging is None:
            return []
        return [path for path in sorted(config.logging.directory.glob('host.log*'), reverse=True)
                if path.name == 'host.log' or re.fullmatch(r'host\.log\.\d{4}-\d{2}-\d{2}', path.name)]

    @app.get('/api/host/log-files')
    async def files(_: str = Depends(user)):
        return {'enabled': config.logging is not None, 'timezone': 'UTC',
                'items': [{'name': p.name, 'bytes': p.stat().st_size} for p in log_files()]}

    @app.get('/api/host/log-files/{name}')
    async def file(name: str, _: str = Depends(user)):
        paths = {p.name: p for p in log_files()}
        if name not in paths:
            raise HTTPException(404, '没有这一份日志')
        # Diagnostic exports, unlike local files, also mask QQ-shaped numbers.
        return archive({'log': paths[name].read_text(encoding='utf-8')})
