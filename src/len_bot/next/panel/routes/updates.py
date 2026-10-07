"""Authenticated handoff to the installation's independent update controller."""

from collections.abc import Callable
from importlib.metadata import version
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx


def register_host_updates(app: FastAPI, *, root: Path, user: Callable[[Request], str]) -> None:
    def control() -> dict | None:
        file = root / '.runtime/update-control.json'
        if not file.exists():
            return None
        value = json.loads(file.read_text(encoding='utf-8'))
        if value['protocol'] != 1:
            raise ValueError('请先更新安装控制器；控制协议不匹配')
        return value

    async def request_control(path: str) -> dict | list:
        reference = control()
        if reference is None:
            raise HTTPException(409, '源码安装不支持面板换版；请使用 Git 和离线迁移命令')
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(reference['endpoint'] + '/api/' + path,
                                        headers={'Authorization': 'Bearer ' + reference['token']})
            if response.is_error:
                raise HTTPException(502, f'更新控制器 HTTP {response.status_code}: {response.text[:2000]}')
            return response.json()

    @app.get('/api/host/updates')
    async def current(_: str = Depends(user)):
        try:
            reference = control()
            return {'version': version('len-bot'), 'managed': reference is not None,
                    'status': None if reference is None else await request_control('status')}
        except (OSError, ValueError, httpx.HTTPError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/api/host/updates/releases')
    async def releases(_: str = Depends(user)):
        try:
            if control() is not None:
                return await request_control('releases')
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get('https://api.github.com/repos/lendevs/LenBot/releases?per_page=100')
                response.raise_for_status()
                return [{'tag': item['tag_name'], 'prerelease': item['prerelease'], 'notes': item['body']}
                        for item in response.json() if not item['draft']]
        except (OSError, ValueError, httpx.HTTPError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post('/api/host/updates/session')
    async def session(_: str = Depends(user)):
        try:
            return await request_control('session')
        except (OSError, ValueError, httpx.HTTPError) as error:
            raise HTTPException(422, str(error)) from error
