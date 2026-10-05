"""Panel controls for the explicitly configured dedicated account browser."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ...browser.client import AccountBrowser, AccountBrowserSettings
from ...configuration.editing import _prepare, _read_saved
from ...browser.tasks import TaskBrowser
from ...work.store import Task, TaskStore


class BrowserChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    settings: AccountBrowserSettings | None


class ReleaseBrowser(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scene: str
    task_id: int = Field(gt=0)
    session_id: str | None = None


class BrowserState(BaseModel):
    running: AccountBrowserSettings | None
    saved: AccountBrowserSettings | None
    restart_required: bool
    occupied: list[Task]


class TaskBrowserConnection(BaseModel):
    browser: dict[str, JsonValue] | None
    session: dict[str, JsonValue] | None


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
        return BrowserState(running=running, saved=saved.account_browser,
                            restart_required=saved.account_browser != running,
                            occupied=records.browser_in_use())

    @app.get('/api/host/browser', response_model=BrowserState)
    async def state(_: str = Depends(user)):
        try:
            return view(await asyncio.to_thread(_read_saved, root))
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.put('/api/host/browser', response_model=BrowserState)
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

    @app.post('/api/host/tasks/{task_id}/browser/status', response_model=TaskBrowserConnection)
    async def task_status(task_id: int, scene: str, _: str = Depends(user)):
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主没有此场景')
        try:
            item = records.get(scene, task_id)
            if not item.account_browser:
                raise ValueError('公共浏览器属于任务容器，不使用账号浏览 daemon')
            client = service()
            state = await client.rpc('system.status', {})
            try:
                browsers, sessions = state['browsers'], state['sessions']
            except KeyError as error:
                raise ValueError(f'Invalid browser system.status: {state!r}') from error
            if (not isinstance(browsers, list) or not isinstance(sessions, list)
                    or any(not isinstance(row, dict) or not isinstance(row.get('instance_id'), str) for row in browsers)
                    or any(not isinstance(row, dict) or not isinstance(row.get('session_id'), str) for row in sessions)):
                raise ValueError(f'Invalid browser system.status: {state!r}')
            return {'browser': next((row for row in browsers if row['instance_id'] == client.settings.browser_instance_id), None),
                    'session': next((row for row in sessions if row['session_id'] == item.browser_session), None)}
        except (ValueError, KeyError, OSError, RuntimeError, TimeoutError) as error:
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
                return await TaskBrowser(runtime.config, records, client).release(item, session_id=body.session_id)
            except (ValueError, KeyError, OSError, RuntimeError, TimeoutError) as error:
                raise HTTPException(409, str(error)) from error
