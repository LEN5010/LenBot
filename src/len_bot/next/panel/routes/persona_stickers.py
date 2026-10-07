"""Saved role sticker index and raw assets; running images are immutable snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import stat
import tempfile
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import field_validator

from ...configuration.types import STRICT
from ...config import HostConfig, load_host_config
from .persona import PersonaFileChange, finish_role_write, validate_dependencies
from ....image_assets import MAX_IMAGE_BYTES, inspect_image
from ...runtime.network import NetworkRuntime
from ...persona.profile import PersonaTarget, parse_persona_files, read_persona_files, require_persona_target
from ...persona.stickers import StickerEntry, parse_sticker_index, sticker_entries
import yaml


class StickerAssetName(PersonaTarget):
    model_config = STRICT
    file: str

    @field_validator('file')
    @classmethod
    def relative_asset(cls, value: str) -> str:
        if (value == 'index.yaml' or '\\' in value
                or any(part in {'', '.', '..'} or any(ord(char) < 32 for char in part)
                       for part in value.split('/'))):
            raise ValueError(f'表情原件须为stickers内的相对文件，不能是index.yaml：{value!r}')
        return value


class StickerEntries(PersonaTarget):
    """The sticker index as the panel form edits it; saved as stickers/index.yaml."""
    model_config = STRICT
    entries: list[StickerEntry]


def asset_target(persona: Path, name: str) -> Path:
    root = persona / 'stickers'
    target = root.joinpath(*name.split('/'))
    for path in (root, *target.parents):
        if path == persona:
            break
        if path.is_symlink():
            raise ValueError(f'表情原件不能穿越目录链接：{path}')
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'表情原件不能通过链接或离开stickers：{target}')
    return target


def source_index(persona: Path) -> str | None:
    path = asset_target(persona, 'index.yaml')
    if not path.exists():
        return None
    if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
        raise ValueError(f'表情索引不是常规文件：{path}')
    raw = path.read_bytes()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'{path}: invalid UTF-8: {error}; raw={raw[error.start:error.end + 30]!r}') from error


def atomic_file(target: Path, content: bytes, *, create: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.persona-sticker-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        if create:
            os.link(temporary, target)
        else:
            temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def register_host_persona_stickers(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                                   user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def configured(scene: str) -> tuple[HostConfig, Path]:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前运行宿主没有这一场景')
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError('场景已从保存配置移除，不能操作其表情文件')
        return config, config.scenes[scene].persona

    def selected(scene: str, directory: str) -> tuple[HostConfig, Path]:
        config, path = configured(scene)
        require_persona_target(path, directory)
        return config, path

    def state(scene: str) -> dict:
        config, persona = configured(scene)
        index = source_index(persona)
        files = []
        directory = persona / 'stickers'

        def listing_error(error: OSError) -> None:
            raise error

        if directory.exists():
            if not directory.is_dir():
                raise ValueError(f'表情资源路径不是目录：{directory}')
            for folder, directories, filenames in os.walk(directory, followlinks=False, onerror=listing_error):
                for name in (*directories, *filenames):
                    actual = Path(folder) / name
                    relative = actual.relative_to(directory).as_posix()
                    asset_target(persona, relative)
                    mode = actual.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode):
                        continue
                    if not stat.S_ISREG(mode):
                        raise ValueError(f'表情资源不是常规文件：{actual}')
                    if relative != 'index.yaml':
                        files.append({'file': relative, 'bytes': actual.stat().st_size})
        running = runtime.chats[scene]
        return {'scene': scene, 'saved_path': str(persona), 'running_path': str(running.config.persona),
                'index_content': index, 'files': sorted(files, key=lambda item: item['file']),
                'entries': None if index is None else [entry.model_dump() for entry in
                                                       sticker_entries(directory / 'index.yaml', index)],
                'affected_scenes': [key for key, value in config.scenes.items() if value.persona == persona],
                'running_entries': [{'file': item.file, 'description': item.description,
                                     'emotions': item.emotions, 'tags': item.tags}
                                    for item in running.persona.stickers.values()],
                'restart_required': None}

    def save_index(scene: str, directory: str, content: str) -> dict:
        config, persona = selected(scene, directory)
        images = parse_sticker_index(persona, content)
        candidate = parse_persona_files(persona, read_persona_files(persona), stickers=images)
        validate_dependencies(config, persona, candidate)
        target = asset_target(persona, 'index.yaml')
        atomic_file(target, content.encode('utf-8'), create=not target.exists())
        result = state(scene)
        running = runtime.chats[scene]
        result['restart_required'] = candidate.stickers != running.persona.stickers or persona != running.config.persona
        return result

    def upload(scene: str, item: StickerAssetName, data: bytes) -> dict:
        _, persona = selected(scene, item.directory)
        index = source_index(persona)
        if index is None:
            raise ValueError('尚无stickers/index.yaml；先明确保存索引（可为[]），再上传原件')
        sticker_entries(persona / 'stickers' / 'index.yaml', index)
        target = asset_target(persona, item.file)
        if target.exists():
            raise FileExistsError(f'上传原件不覆盖已有文件：{target}')
        mime, width, height, animated = inspect_image(data)
        atomic_file(target, data, create=True)
        return {'file': item.file, 'bytes': len(data), 'mime_type': mime, 'width': width,
                'height': height, 'animated': animated, 'registered_automatically': False,
                'notice': '原件已存到宿主角色包；未改索引、运行表情或向平台发送。请明确编辑索引后保存。'}

    def delete(scene: str, item: StickerAssetName) -> dict:
        _, persona = selected(scene, item.directory)
        index = source_index(persona)
        if index is None:
            raise ValueError('无表情索引，不能确认原件是否被引用；先明确建立索引')
        if any(entry.file == item.file for entry in sticker_entries(persona / 'stickers' / 'index.yaml', index)):
            raise ValueError('原件仍被保存索引引用；先明确删除索引项并保存，再删除原件')
        target = asset_target(persona, item.file)
        if not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode):
            raise ValueError(f'只删除常规表情原件，不删除目录：{target}')
        target.unlink()
        return {'file': item.file, 'deleted': True, 'notice': '未引用原件已删除；运行内存表情快照未改。'}

    async def operation(function: Callable[..., dict], *args: object, writing: bool = False) -> dict:
        try:
            async with write_lock:
                return await finish_role_write(function, *args) if writing else await asyncio.to_thread(function, *args)
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.get('/api/host/scenes/{scene}/persona-sticker-files')
    async def listing(scene: str, response: Response, _: str = Depends(user)):
        response.headers['Cache-Control'] = 'no-store'
        return await operation(state, scene)

    @app.put('/api/host/scenes/{scene}/persona-sticker-files/index')
    async def index_write(scene: str, item: PersonaFileChange, _: str = Depends(user)):
        return await operation(save_index, scene, item.directory, item.content, writing=True)

    @app.put('/api/host/scenes/{scene}/persona-sticker-files/entries')
    async def entries_write(scene: str, item: StickerEntries, _: str = Depends(user)):
        content = yaml.safe_dump([entry.model_dump() for entry in item.entries], allow_unicode=True, sort_keys=False)
        return await operation(save_index, scene, item.directory, content, writing=True)

    @app.post('/api/host/scenes/{scene}/persona-sticker-files/image')
    async def image_upload(scene: str, file: UploadFile, name: str = Form(), directory: str = Form(min_length=1), _: str = Depends(user)):
        try:
            data = await file.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413, f'表情原件超过{MAX_IMAGE_BYTES}字节')
            try:
                item = StickerAssetName.model_validate({'file': name, 'directory': directory})
            except ValueError as error:
                raise HTTPException(422, f'{error}; raw file={name!r}') from error
            return await operation(upload, scene, item, data, writing=True)
        finally:
            await file.close()

    @app.delete('/api/host/scenes/{scene}/persona-sticker-files/image')
    async def image_delete(scene: str, item: StickerAssetName, _: str = Depends(user)):
        return await operation(delete, scene, item, writing=True)

    @app.get('/api/host/scenes/{scene}/persona-sticker-files/image')
    async def image_read(scene: str, item: Annotated[StickerAssetName, Query()], _: str = Depends(user)):
        def read() -> dict:
            _, persona = selected(scene, item.directory)
            target = asset_target(persona, item.file)
            if not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode):
                raise ValueError(f'表情原件不是常规文件：{target}')
            with target.open('rb') as stream:
                data = stream.read(MAX_IMAGE_BYTES + 1)
            mime, _, _, _ = inspect_image(data)
            return {'data': data, 'mime': mime}

        result = await operation(read)
        return Response(result['data'], media_type=result['mime'], headers={'Cache-Control': 'no-store'})
