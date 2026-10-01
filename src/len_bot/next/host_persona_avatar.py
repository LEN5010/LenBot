"""Authenticated saved-avatar editing and separate running-avatar reads."""

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile

from .config import HostConfig, load_host_config
from .host_persona import finish_role_write
from .image_assets import MAX_IMAGE_BYTES, OriginalImage
from .network import NetworkRuntime
from .persona_avatar import avatar_file, load_avatar, parse_avatar
from .persona import PersonaTarget, require_persona_target


def image_info(image: OriginalImage | None) -> dict | None:
    if image is None:
        return None
    return {'mime_type': image.mime_type, 'bytes': len(image.data), 'width': image.width,
            'height': image.height, 'animated': image.animated}


def register_host_persona_avatar(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                                user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def configured(scene: str) -> tuple[HostConfig, Path]:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前运行宿主没有这一场景')
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError('场景已从保存配置移除，不能编辑其角色头像')
        return config, config.scenes[scene].persona

    def describe(scene: str, config: HostConfig, path: Path, saved: OriginalImage | None) -> dict:
        chat = runtime.chats[scene]
        return {'directory': str(path), 'saved': {'directory': str(path), 'path': str(path / 'avatar.png'), 'image': image_info(saved)},
                'running': {'directory': str(chat.config.persona), 'path': str(chat.config.persona / 'avatar.png'), 'name': chat.persona.name,
                            'image': image_info(chat.persona.avatar)},
                'restart_required': saved != chat.persona.avatar or path != chat.config.persona,
                'affected_scenes': [key for key, value in config.scenes.items() if value.persona == path],
                'notice': '只处理avatar.png原件；当前运行头像不变，不改角色原文、模型或场景绑定，不向平台发送。'}

    def state(scene: str) -> dict:
        config, path = configured(scene)
        return describe(scene, config, path, load_avatar(path))

    def selected(scene: str, directory: str) -> tuple[HostConfig, Path]:
        config, path = configured(scene)
        require_persona_target(path, directory)
        return config, path

    def location(scene: str) -> dict:
        config, path = configured(scene)
        return {'directory': str(path),
                'affected_scenes': [key for key, value in config.scenes.items() if value.persona == path]}

    def save(scene: str, directory: str, data: bytes) -> dict:
        config, path = selected(scene, directory)
        image = parse_avatar(data)
        target = avatar_file(path)
        descriptor, name = tempfile.mkstemp(prefix='.persona-avatar-', dir=path)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(image.data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return describe(scene, config, path, image)

    def delete(scene: str, directory: str) -> dict:
        config, path = selected(scene, directory)
        avatar_file(path).unlink()
        return describe(scene, config, path, None)

    def read(scene: str, view: Literal['saved', 'running'], directory: str) -> dict:
        if view == 'saved':
            _, path = selected(scene, directory)
            image = load_avatar(path)
        else:
            if scene not in runtime.chats:
                raise HTTPException(404, '当前运行宿主没有这一场景')
            if directory != str(runtime.chats[scene].config.persona):
                raise ValueError('运行角色包位置与本次预览选择不同，请重读运行快照')
            image = runtime.chats[scene].persona.avatar
        if image is None:
            raise HTTPException(404, f'{view}角色没有头像原件')
        return {'data': image.data}

    async def operation(function: Callable[..., dict], *args: object, writing: bool = False) -> dict:
        try:
            async with write_lock:
                return await finish_role_write(function, *args) if writing else await asyncio.to_thread(function, *args)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.get('/api/host/scenes/{scene}/persona-avatar')
    async def metadata(scene: str, response: Response, _: str = Depends(user)):
        response.headers['Cache-Control'] = 'no-store'
        return await operation(state, scene)

    @app.get('/api/host/scenes/{scene}/persona-avatar/location')
    async def selected_directory(scene: str, response: Response, _: str = Depends(user)):
        response.headers['Cache-Control'] = 'no-store'
        return await operation(location, scene)

    @app.get('/api/host/scenes/{scene}/persona-avatar/image')
    async def preview(scene: str, view: Literal['saved', 'running'], directory: str, _: str = Depends(user)):
        value = await operation(read, scene, view, directory)
        return Response(value['data'], media_type='image/png', headers={'Cache-Control': 'no-store'})

    @app.put('/api/host/scenes/{scene}/persona-avatar')
    async def upload(scene: str, file: UploadFile, directory: str = Form(min_length=1), _: str = Depends(user)):
        try:
            data = await file.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413, f'角色头像超过{MAX_IMAGE_BYTES}字节')
            return await operation(save, scene, directory, data, writing=True)
        finally:
            await file.close()

    @app.delete('/api/host/scenes/{scene}/persona-avatar')
    async def remove(scene: str, item: PersonaTarget, _: str = Depends(user)):
        return await operation(delete, scene, item.directory, writing=True)
