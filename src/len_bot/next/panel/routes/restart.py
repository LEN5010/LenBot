"""Panel preview and explicit host restart using the existing shutdown path."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from ...config import HostConfig
from ...runtime.lifecycle import HostLifecycle
from ...configuration.editing import _read_saved, restart_summary
from .trials import HostTrials
from ...runtime.network import NetworkRuntime
from ...work.store import TaskStore


def register_host_restart(app: FastAPI, *, root: Path, running: HostConfig,
                          runtime: NetworkRuntime, trials: HostTrials, lifecycle: HostLifecycle,
                          user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    @app.get('/api/host/ready')
    async def ready(response: Response):
        # Login sessions expire with a process, so reconnect only needs public process identity.
        response.headers['Cache-Control'] = 'no-store'
        return lifecycle.process()

    def preview() -> dict:
        saved = _read_saved(root)
        records = TaskStore(runtime.store)
        panel = lambda value: None if value is None else {'host': value.host, 'port': value.port}
        return {
            'process': lifecycle.process(),
            'restartable': lifecycle.restartable and lifecycle.shutdown is not None,
            'intent': lifecycle.intent,
            'pending': restart_summary(root, running, {scene: chat.persona for scene, chat in runtime.chats.items()}),
            'panel': {'running': panel(running.panel), 'saved': panel(saved.panel)},
            'chats': [scene for scene, runner in runtime.runners.items() if runner.execution.locked()],
            'tasks': [{'id': item.id, 'scene': item.scene, 'goal': item.goal, 'status': item.status,
                       'account_browser': item.account_browser} for item in records.active()],
            'queued_tasks': len(records.queued()),
            'browsers': [{'id': item.id, 'scene': item.scene, 'session': item.browser_session}
                         for item in records.browser_in_use()],
            'trials': [{'id': item.id, 'scene': item.scene} for item in trials.records.values()
                       if item.stopped is None],
        }

    @app.get('/api/host/restart')
    async def read_restart(_: str = Depends(user)):
        async with write_lock:
            try:
                return preview()
            except (ValueError, OSError) as error:
                raise HTTPException(422, str(error)) from error

    @app.post('/api/host/restart')
    async def restart(_: str = Depends(user)):
        async with write_lock:
            try:
                lifecycle.require_restart()
                result = preview()
            except RuntimeError as error:
                raise HTTPException(409, str(error)) from error
            except (ValueError, OSError) as error:
                raise HTTPException(422, str(error)) from error
            return JSONResponse({**result, 'intent': 'restart'}, status_code=202,
                                background=BackgroundTask(lifecycle.restart))
