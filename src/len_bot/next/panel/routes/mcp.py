"""Authenticated MCP configuration and explicit actions on the current runtime binding."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ...config import HostConfig, _read_root
from .settings import _body
from ...configuration.editing import _prepare, _read_saved
from ...configuration.mcp import SERVICE_NAME
from ...runtime.network import NetworkRuntime


class ServiceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    settings: dict[str, object]


def masked(settings: dict) -> dict:
    result = deepcopy(settings)
    transport = result['transport']
    field = 'env' if transport['type'] == 'stdio' else 'headers'
    transport[field] = {key: None for key in transport.get(field, {})}
    return result


def register_host_mcp(app: FastAPI, *, root: Path, runtime: NetworkRuntime, running: HostConfig,
                      user, write_lock: asyncio.Lock) -> None:
    def config_state() -> dict:
        saved = _read_saved(root)
        _, raw = _read_root(root)
        return {'saved': {name: masked(value) for name, value in raw.get('mcp', {}).items()},
                'scenes': list(saved.scenes), 'restart_required': saved.mcp != running.mcp}

    async def state() -> dict:
        async with write_lock:
            result = await asyncio.to_thread(config_state)
        return {**result, 'running': [] if runtime.mcp is None else runtime.mcp.state()}

    def require_name(name: str) -> None:
        if SERVICE_NAME.fullmatch(name) is None or '__' in name:
            raise HTTPException(422, '服务名须为1–24个小写字母/数字/下划线，字母开头且不含双下划线')

    async def save(edit) -> dict:
        async with write_lock:
            try:
                path, temporary, _ = await asyncio.to_thread(_prepare, root, edit)
                try:
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500, str(error)) from error
        return await state()

    @app.get('/api/host/mcp')
    async def get(_: str = Depends(user)):
        try:
            return await state()
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500, str(error)) from error

    @app.put('/api/host/mcp/{name}')
    async def put(name: str, request: Request, _: str = Depends(user)):
        require_name(name)
        body = await _body(request, ServiceChange)
        def edit(source: dict, saved: HostConfig) -> None:
            setting = deepcopy(body.settings)
            transport = setting.get('transport')
            if isinstance(transport, dict) and transport.get('type') in {'stdio', 'http'}:
                field = 'env' if transport['type'] == 'stdio' else 'headers'
                values = transport.get(field, {})
                prior = source.get('mcp', {}).get(name, {}).get('transport', {})
                old = prior.get(field, {}) if prior.get('type') == transport['type'] else {}
                if isinstance(values, dict):
                    for key, value in values.items():
                        if value is None:
                            if key not in old:
                                raise ValueError(f'{field}.{key} 尚未保存，不能用null保持原值')
                            values[key] = old[key]
            source.setdefault('mcp', {})[name] = setting
        return await save(edit)

    @app.delete('/api/host/mcp/{name}')
    async def delete(name: str, _: str = Depends(user)):
        require_name(name)
        def edit(source: dict, saved: HostConfig) -> None:
            if name not in saved.mcp:
                raise ValueError(f'根配置中没有MCP服务 {name}')
            del source['mcp'][name]
        return await save(edit)

    async def action(name: str, connect: bool) -> dict:
        if not runtime.accepting:
            raise HTTPException(409, '宿主正在停止')
        if runtime.mcp is None or name not in runtime.mcp.connections:
            raise HTTPException(409, '当前运行绑定中没有此服务；保存的新配置须重启后生效')
        try:
            result = (await runtime.mcp.connect(name) if connect else await runtime.mcp.disconnect(name))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        runtime.refresh_external_tools()
        return {'result': result, 'binding': 'running', **await state()}

    @app.post('/api/host/mcp/{name}/connect')
    async def connect(name: str, _: str = Depends(user)):
        return await action(name, True)

    @app.post('/api/host/mcp/{name}/disconnect')
    async def disconnect(name: str, _: str = Depends(user)):
        return await action(name, False)
