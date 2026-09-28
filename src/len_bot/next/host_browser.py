"""Panel controls for the explicitly configured dedicated account browser."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .account_browser import AccountBrowser, AccountBrowserSettings
from .host_settings import _prepare, _read_saved
from .tasks_store import TaskStore, TERMINAL


class BrowserChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    settings: AccountBrowserSettings | None


class ReleaseBrowser(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scene: str
    task_id: int = Field(gt=0)
    session_id: str | None = None


def register_host_browser(app: FastAPI, *, root: Path, runtime, user, write_lock):
    running = runtime.config.account_browser
    browser = None if running is None else AccountBrowser(running)
    records = TaskStore(runtime.store)
    control = asyncio.Lock()

    def service():
        if browser is None:
            raise HTTPException(409, '账号浏览未在当前进程启用；保存配置后自行重启')
        return browser

    def view(saved):
        return {'running': None if running is None else running.model_dump(mode='json'),
                'saved': None if saved.account_browser is None else saved.account_browser.model_dump(mode='json'),
                'restart_required': saved.account_browser != running,
                'occupied': [asdict(item) for item in records.browser_in_use()]}

    @app.get('/api/host/browser')
    async def state(_: str = Depends(user)):
        try:
            return view(await asyncio.to_thread(_read_saved, root))
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.put('/api/host/browser')
    async def save(request: Request, _: str = Depends(user)):
        try:
            body = BrowserChange.model_validate_json(await request.body())
        except ValidationError as error:
            detail = '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors(include_input=False))
            raise HTTPException(422, detail) from error
        async with write_lock:
            if records.browser_in_use() and body.settings != running:
                raise HTTPException(409, '仍有账号浏览器占用，先完成原会话清理再改绑定')
            def edit(source, saved):
                source['account_browser'] = None if body.settings is None else body.settings.model_dump(mode='json')
            try:
                path, temporary, candidate = await asyncio.to_thread(_prepare, root, edit)
                await asyncio.to_thread(temporary.replace, path)
                return view(candidate)
            except (ValueError, OSError) as error:
                raise HTTPException(422, str(error)) from error

    @app.post('/api/host/browser/status')
    async def status(_: str = Depends(user)):
        try:
            return await service().rpc('system.status', {})
        except (ValueError, OSError, RuntimeError, TimeoutError) as error:
            raise HTTPException(502, str(error)) from error

    @app.post('/api/host/browser/pair')
    async def pair(_: str = Depends(user)):
        async with control:
            try:
                return await service().management('pair')
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                raise HTTPException(502, str(error)) from error

    @app.get('/api/host/browser/devices')
    async def devices(_: str = Depends(user)):
        try:
            return await service().management('devices')
        except (ValueError, OSError, RuntimeError, TimeoutError) as error:
            raise HTTPException(502, str(error)) from error

    @app.delete('/api/host/browser/devices/{device}')
    async def revoke(device: str, _: str = Depends(user)):
        async with control:
            try:
                return await service().management('revoke', device)
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                raise HTTPException(502, str(error)) from error

    @app.post('/api/host/browser/release')
    async def release(body: ReleaseBrowser, _: str = Depends(user)):
        async with control:
            client = service()
            if body.scene not in runtime.chats:
                raise HTTPException(404, '当前宿主没有此场景')
            try:
                item = records.get(body.scene, body.task_id)
                if item.status not in TERMINAL or not item.browser_active:
                    raise ValueError('只清理已结束但仍占用浏览器的任务；活动任务应先取消')
                sessions = await client.sessions(bound=False)
                own = [row['session_id'] for row in sessions if row['browser_instance_id'] == running.browser_instance_id]
                target = item.browser_session if body.session_id is None else body.session_id
                if item.browser_session is not None and target != item.browser_session:
                    raise ValueError('只能关闭此任务已绑定的实际会话')
                if target is not None and any(row['session_id'] == target and row['browser_instance_id'] != running.browser_instance_id for row in sessions):
                    raise ValueError('原任务会话仍在其他浏览器绑定上；恢复原配置清理，不能按当前空配置释放')
                if target is not None and target in own:
                    await client.stop(target)
                    own.remove(target)
                elif target is not None and target != item.browser_session:
                    raise ValueError('所选会话不属于当前专用浏览器')
                if own:
                    raise ValueError(f'专用浏览器仍有会话：{own!r}；请明确选择实际会话清理')
                records.browser_binding(item.scene, item.id, active=False, session=None)
                records.add_event(item.scene, item.id, 'browser_released', {'session_id': target, 'confirmed_empty': True})
                return {'released': True}
            except (ValueError, KeyError, OSError, RuntimeError, TimeoutError) as error:
                raise HTTPException(409, str(error)) from error
