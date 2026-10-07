"""Authenticated handoff to the installation's independent update controller."""

from collections.abc import Callable
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx

from ...runtime.releases import CHECK_TIMEOUT_SECONDS, GITHUB_RELEASES, ReleaseCheck, current_version, github_releases

ERRORS = (OSError, ValueError, KeyError, TimeoutError, httpx.HTTPError)


def register_host_updates(app: FastAPI, *, root: Path, user: Callable[[Request], str],
                          check_enabled: bool = True) -> ReleaseCheck:
    """Register the update routes; the returned check is started by the panel's lifespan when enabled."""
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

    async def load_releases() -> list[dict]:
        # A managed installation asks its own controller, which reads the release source it was installed from.
        if control() is not None:
            return await request_control('releases')
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS, headers={
                'Accept': 'application/vnd.github+json', 'User-Agent': 'LenBot/' + current_version()}) as client:
            response = await client.get(GITHUB_RELEASES)
            if response.is_error:
                # GitHub explains rate limits and outages in the body; keep it instead of a bare status.
                raise ValueError(f'GitHub HTTP {response.status_code}: {response.text[:500]}')
            return github_releases(response.json())

    releases = ReleaseCheck(load_releases, enabled=check_enabled)

    @app.get('/api/host/updates')
    async def current(_: str = Depends(user)):
        try:
            reference = control()
            return {'version': releases.current, 'managed': reference is not None,
                    'status': None if reference is None else await request_control('status'),
                    'check': releases.state()}
        except ERRORS as error:
            raise HTTPException(422, str(error)) from error

    @app.get('/api/host/updates/releases')
    async def published(_: str = Depends(user)):
        # An explicit check works even with the background check turned off.
        try:
            return await releases.refresh()
        except ERRORS as error:
            raise HTTPException(422, f'{type(error).__name__}: {error}') from error

    @app.post('/api/host/updates/session')
    async def session(_: str = Depends(user)):
        try:
            return await request_control('session')
        except ERRORS as error:
            raise HTTPException(422, str(error)) from error

    return releases
