"""First-run local configuration editor; never starts the business runtime."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
import yaml

from .config import Binding, HostConfig, OneBotSettings, Provider
from .persona import Persona, load_persona
from len_bot.web.auth import hash_password


class FirstSetup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', hide_input_in_errors=True)
    bot_qq: str
    owner_qq: str | None
    timezone: str
    delivery: Literal['simulated', 'onebot']
    onebot: OneBotSettings
    provider: Provider
    mind: Binding
    voice: Binding
    scene: str
    persona_id: str = Field(pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$', max_length=64)
    persona_name: str = Field(min_length=1)
    brief: str = Field(min_length=1)
    voice_text: str = Field(min_length=1)
    boundaries: str
    panel_port: int = Field(ge=1, le=65535)
    username: str = Field(min_length=1)
    password: str = Field(min_length=8, repr=False)


def initialize(root: Path, item: FirstSetup) -> dict:
    """Validate first, create only new files, publish the root config last."""
    config_path = root / 'lenbot.config.json'
    role_path = root / 'personas' / item.persona_id
    if config_path.exists():
        raise FileExistsError('根配置已经存在；初始化不覆盖，继续使用正常管理面板')
    if role_path.exists():
        raise FileExistsError(f'角色目录已经存在，请换一个新角色目录名：{role_path}')
    persona = Persona(id=item.persona_id, name=item.persona_name, brief=item.brief,
                      behavior=item.brief, self_reference=['我'], aliases=[], tools='all', skills=[],
                      styles=[], voice=item.voice_text, boundaries=item.boundaries, examples=[])
    source = {
        'mode': 'isolated-multi', 'bot_qq': item.bot_qq, 'owner_qq': item.owner_qq,
        'timezone': item.timezone, 'delivery': item.delivery, 'database': 'state/lenbot.sqlite3',
        'onebot': item.onebot.model_dump(mode='json'),
        'models': {'providers': {'primary': item.provider.model_dump(mode='json')},
                   'roles': {'mind': item.mind.model_dump(mode='json'), 'voice': item.voice.model_dump(mode='json')}},
        'panel': {'host': '127.0.0.1', 'port': item.panel_port, 'username': item.username,
                  'password_hash': hash_password(item.password)},
        'scenes': {item.scene: {'persona': f'personas/{item.persona_id}', 'attention': {'only_direct': True}}},
    }
    # Validate all cross-field requirements before creating any role/config files.
    HostConfig.model_validate_json(json.dumps(source))
    role_path.parent.mkdir(parents=True, exist_ok=True)
    role_path.mkdir(mode=0o700)
    metadata = persona.model_dump(exclude={'voice', 'boundaries', 'examples'})
    (role_path / 'persona.yaml').write_text(yaml.safe_dump(metadata, allow_unicode=True), encoding='utf-8')
    (role_path / 'voice.md').write_text(persona.voice, encoding='utf-8')
    (role_path / 'boundaries.md').write_text(persona.boundaries, encoding='utf-8')
    (role_path / 'examples.yaml').write_text('[]\n', encoding='utf-8')
    load_persona(role_path)
    fd, filename = tempfile.mkstemp(prefix='.lenbot-setup-', suffix='.json', dir=root)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(source, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # link publishes without replacing a root file created by another process.
        os.link(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {'saved': True, 'config': str(config_path), 'persona': str(role_path),
            'panel_url': f'http://127.0.0.1:{item.panel_port}', 'delivery': item.delivery,
            'next': '现在重新执行 uv run len-bot，然后登录面板，先到对话测试里聊几句。'}


def create_setup_app(root: Path, token: str, completed: asyncio.Event) -> FastAPI:
    app = FastAPI(title='LenBot 首次配置')
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])
    lock = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={'detail': [
            {key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()
        ]})

    async def authorize(x_setup_token: str = Header(default='')):
        if not secrets.compare_digest(x_setup_token, token):
            raise HTTPException(401, '请使用本次启动在终端显示的初始化链接')

    @app.get('/', response_class=HTMLResponse)
    async def page():
        return (Path(__file__).with_name('setup.html')).read_text(encoding='utf-8')

    @app.post('/api/setup', dependencies=[Depends(authorize)])
    async def save(item: FirstSetup):
        async with lock:
            try:
                result = await asyncio.to_thread(initialize, root, item)
            except FileExistsError as error:
                raise HTTPException(409, str(error)) from error
            except (ValueError, OSError) as error:
                # Pydantic errors omit inputs (including provider keys and passwords).
                if isinstance(error, ValidationError):
                    detail = '; '.join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors(include_input=False))
                else:
                    detail = str(error)
                raise HTTPException(422, detail) from error
            completed.set()
            return result

    return app


async def run_setup(root: Path) -> None:
    token = secrets.token_urlsafe(32)
    completed = asyncio.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        app = create_setup_app(root, token, completed)
        server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
        print(f'尚无根配置。请打开 http://127.0.0.1:{port}/#token={token}\n'
              '这里只保存首次配置，不连接 QQ、不调用模型；保存后重新执行 uv run len-bot。', flush=True)
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        saving = asyncio.create_task(completed.wait())
        try:
            await asyncio.wait([serving, saving], return_when=asyncio.FIRST_COMPLETED)
        finally:
            server.should_exit = True
            saving.cancel()
            await asyncio.gather(saving, return_exceptions=True)
            await serving
