"""Edit actual configured role files without replacing the running role."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator
import yaml

from .chat_tools import build_tools
from .config import STRICT, HostConfig, load_host_config
from .learning_store import LearningStore
from .network import NetworkRuntime
from .skills import select_skills
from .plugin_manifest import scene_skill_catalog
from .persona import (Example, Persona, PersonaTarget, Style, load_persona, parse_persona_files,
                      read_persona_files, require_persona_target)
from .persona_knowledge import parse_knowledge_document
from .persona_packages import MAX_UPLOAD_BYTES, export_package, import_package


PersonaFilename = Literal["persona.yaml", "voice.md", "boundaries.md", "examples.yaml"]


class PersonaFileChange(PersonaTarget):
    model_config = STRICT
    content: str


class PersonaProfile(BaseModel):
    """The parts of a role package an operator edits in the panel form; id, tools and skills stay as saved."""

    model_config = STRICT
    name: str
    brief: str
    behavior: str
    self_reference: list[str]
    aliases: list[str]
    styles: list[Style]
    example_tags: list[str]
    voice: str
    boundaries: str
    examples: list[Example]


class PersonaProfileChange(PersonaTarget):
    model_config = STRICT
    profile: PersonaProfile


PROFILE_METADATA = ('name', 'brief', 'behavior', 'self_reference', 'aliases', 'styles', 'example_tags')


def profile_files(files: dict[str, str], profile: PersonaProfile) -> dict[str, str]:
    """Rewrite the four role files from the form; other persona.yaml fields keep their saved values."""
    metadata = yaml.safe_load(files['persona.yaml'])
    if not isinstance(metadata, dict):
        raise ValueError('persona.yaml 不是 YAML 对象')
    values = profile.model_dump(exclude_none=True)
    metadata.update({key: values[key] for key in PROFILE_METADATA})
    examples = [{key: value for key, value in item.items() if key != 'tags' or value} for item in values['examples']]
    dump = lambda value: yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    return {'persona.yaml': dump(metadata), 'voice.md': profile.voice, 'boundaries.md': profile.boundaries,
            'examples.yaml': dump(examples)}


class ExpressionExample(PersonaTarget):
    """An operator-confirmed example taken from one adopted expression; text may be edited first."""

    model_config = STRICT
    expression_id: int
    context: str = Field(min_length=1)
    line: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)

    @field_validator("context", "line")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("样例的场景和台词不能只包含空白")
        return value

    @field_validator("tags")
    @classmethod
    def nonblank_tags(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("标签不能为空白或重复")
        return values


async def finish_role_write[T](operation: Callable[..., T], *args: object) -> T:
    """Keep the caller's protected role-write lifetime until already-started filesystem work ends."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            task.result()
        except BaseException as error:
            cancelled.add_note(f'Role write also failed: {type(error).__name__}: {error}')
            raise cancelled from error
        raise


class KnowledgeFilename(PersonaTarget):
    model_config = STRICT
    filename: str

    @field_validator('filename')
    @classmethod
    def relative_markdown(cls, value: str) -> str:
        if (not value.endswith('.md') or '\\' in value
                or any(part in {'', '.', '..'} or any(ord(char) < 32 for char in part)
                       for part in value.split('/'))):
            raise ValueError(f'知识文件须为knowledge目录内的相对Markdown路径：{value!r}')
        return value


class KnowledgeChange(KnowledgeFilename):
    content: str


def knowledge_target(root: Path, filename: str) -> Path:
    directory = root / 'knowledge'
    target = directory.joinpath(*filename.split('/'))
    for ancestor in (directory, *target.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise ValueError(f'角色资料编辑不穿越目录链接：{ancestor}')
    if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f'角色资料编辑不能通过链接或离开knowledge目录：{target}')
    return target


def validate_dependencies(config: HostConfig, path: Path, candidate: Persona) -> list[str]:
    affected = [key for key, value in config.scenes.items() if value.persona == path]
    for key in affected:
        build_tools(config.scene_config(key), candidate, platform=config.delivery == 'onebot')
        if config.worker is not None:
            select_skills(scene_skill_catalog(config, key), candidate.skills)
    return affected


def append_example(content: str, example: dict) -> str:
    """Append one block item and keep the operator's existing text, comments and order."""
    try:
        current = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError(f"examples.yaml 不是合法 YAML：{error}") from error
    if not isinstance(current, list):
        raise ValueError("examples.yaml 不是 YAML 列表")
    if any(isinstance(item, dict) and item.get("context") == example["context"] and item.get("line") == example["line"]
           for item in current):
        raise ValueError("examples.yaml 已有相同场景和台词的样例")
    block = yaml.safe_dump([example], allow_unicode=True, sort_keys=False)
    if current:
        updated = content + ("" if content.endswith("\n") else "\n") + block
    else:
        node = yaml.compose(content)
        # Replace only the empty sequence, retaining surrounding comments/document markers.
        updated = content[:node.start_mark.index] + "\n" + block.rstrip("\n") + content[node.end_mark.index:]
    not_block = "examples.yaml 不是可在末尾追加的块状列表；请在角色文件编辑器里手动添加"
    try:
        appended = yaml.safe_load(updated)
    except yaml.YAMLError as error:
        raise ValueError(not_block) from error
    if appended != [*current, example]:
        raise ValueError(not_block)
    return updated


def register_host_persona(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                          user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def require_scene(scene: str) -> None:
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")

    @app.get('/api/host/scenes/{scene}/persona-package')
    async def download_package(scene: str, _: str = Depends(user)):
        require_scene(scene)

        def read() -> bytes:
            saved = load_host_config(root)
            if scene not in saved.scenes:
                raise ValueError('所选场景已从保存配置移除，不能导出其角色文件')
            return export_package(saved.scenes[scene].persona)

        try:
            async with write_lock:
                data = await asyncio.to_thread(read)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error
        return Response(data, media_type='application/zip', headers={
            'Content-Disposition': 'attachment; filename="persona.zip"', 'Cache-Control': 'no-store'})

    @app.post('/api/host/personas/import')
    async def upload_package(file: UploadFile, name: str = Form(min_length=1, max_length=64),
                             _: str = Depends(user)):
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, '角色ZIP上传超过64MiB')
            async with write_lock:
                return await finish_role_write(import_package, root, name, data)
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error
        finally:
            await file.close()

    @app.get('/api/host/scenes/{scene}/persona-location')
    async def location(scene: str, response: Response, _: str = Depends(user)):
        require_scene(scene)
        response.headers['Cache-Control'] = 'no-store'
        def read() -> dict:
            saved = load_host_config(root)
            if scene not in saved.scenes:
                raise ValueError('场景已从保存配置移除，不能读取其角色位置')
            path = saved.scenes[scene].persona
            return {'saved_path': str(path), 'running_path': str(runtime.chats[scene].config.persona),
                    'affected_scenes': [key for key, value in saved.scenes.items() if value.persona == path]}
        try:
            async with write_lock:
                return await asyncio.to_thread(read)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500, f'{type(error).__name__}: {error}') from error

    def files_state(scene: str, edit: tuple[str, str, str] | None = None) -> dict:
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError("此场景已从保存配置移除；当前运行角色保留到重启，不再编辑其文件")
        path = config.scenes[scene].persona
        if edit is not None:
            require_persona_target(path, edit[2])
        files = read_persona_files(path)
        if edit is not None:
            files[edit[0]] = edit[1]
        candidate = parse_persona_files(path, files)
        affected = validate_dependencies(config, path, candidate)
        if edit is not None:
            descriptor, name = tempfile.mkstemp(prefix=".persona-", dir=path)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(edit[1])
                temporary.replace(path / edit[0])
            finally:
                temporary.unlink(missing_ok=True)
        running_persona = runtime.chats[scene].persona
        running = running_persona.model_dump()
        stickers_restart_required = candidate.stickers != running_persona.stickers
        return {"saved": files, "running": running, 'saved_path': str(path),
                'running_path': str(runtime.chats[scene].config.persona),
                "restart_required": candidate.model_dump() != running or stickers_restart_required
                                    or candidate.knowledge != running_persona.knowledge
                                    or candidate.avatar != running_persona.avatar
                                    or path != runtime.chats[scene].config.persona,
                "stickers_restart_required": stickers_restart_required,
                'avatar_restart_required': candidate.avatar != running_persona.avatar,
                "affected_scenes": affected}

    def knowledge_state(scene: str, change: KnowledgeChange | KnowledgeFilename | None = None,
                        action: Literal['create', 'write', 'delete'] | None = None) -> dict:
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError('场景已从保存配置移除，不能编辑其角色资料')
        path = config.scenes[scene].persona
        if change is not None:
            require_persona_target(path, change.directory)
        candidate = load_persona(path)
        if change is not None:
            target = knowledge_target(path, change.filename)
            if action == 'create':
                if target.exists():
                    raise FileExistsError(f'角色资料新建不覆盖已有文件：{target}')
            elif change.filename not in candidate.knowledge:
                raise FileNotFoundError(f'角色包没有此知识文件：{target}')
            documents = dict(candidate.knowledge)
            if isinstance(change, KnowledgeChange):
                documents[change.filename] = parse_knowledge_document(target, change.content)
            else:
                del documents[change.filename]
            candidate = candidate.model_copy(update={'knowledge': documents})
        affected = validate_dependencies(config, path, candidate)
        if change is not None:
            if isinstance(change, KnowledgeChange):
                target.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix='.persona-knowledge-', dir=target.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, 'wb') as stream:
                        stream.write(change.content.encode('utf-8'))
                    if action == 'create':
                        os.link(temporary, target)
                    else:
                        temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                target.unlink()
        running = runtime.chats[scene]
        current_document = None
        if isinstance(change, KnowledgeChange):
            saved_document = candidate.knowledge[change.filename]
            running_document = running.persona.knowledge.get(change.filename)
            current_document = {'filename': change.filename, 'saved_path': str(path), 'content': saved_document.content,
                                'tags': saved_document.tags, 'running_content':
                                None if running_document is None else running_document.content}
        return {'scene': scene, 'saved_path': str(path), 'running_path': str(running.config.persona),
                'saved_persona': {'id': candidate.id, 'name': candidate.name},
                'running_persona': {'id': running.persona.id, 'name': running.persona.name},
                'restart_required': candidate.knowledge != running.persona.knowledge or path != running.config.persona,
                'affected_scenes': affected,
                'document': current_document,
                'files': [{'filename': filename, 'tags': document.tags, 'chars': len(document.content)}
                          for filename, document in sorted(candidate.knowledge.items())]}

    @app.get('/api/host/scenes/{scene}/persona-knowledge')
    async def knowledge_listing(scene: str, response: Response, _: str = Depends(user)):
        require_scene(scene)
        response.headers['Cache-Control'] = 'no-store'
        try:
            async with write_lock:
                return await asyncio.to_thread(knowledge_state, scene)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.get('/api/host/scenes/{scene}/persona-knowledge/document')
    async def knowledge_document(scene: str, response: Response,
                                 item: Annotated[KnowledgeFilename, Query()], _: str = Depends(user)):
        require_scene(scene)
        response.headers['Cache-Control'] = 'no-store'

        def read() -> dict:
            config = load_host_config(root)
            if scene not in config.scenes:
                raise ValueError('场景已从保存配置移除，不能读取其角色资料')
            path = config.scenes[scene].persona
            require_persona_target(path, item.directory)
            candidate = load_persona(path)
            if item.filename not in candidate.knowledge:
                raise FileNotFoundError(f'保存角色包没有此资料：{item.filename!r}')
            saved = candidate.knowledge[item.filename]
            running = runtime.chats[scene].persona.knowledge.get(item.filename)
            return {'filename': item.filename, 'saved_path': str(path), 'content': saved.content, 'tags': saved.tags,
                    'running_content': None if running is None else running.content}

        try:
            async with write_lock:
                return await asyncio.to_thread(read)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    async def write_knowledge(scene: str, item: KnowledgeChange | KnowledgeFilename,
                              action: Literal['create', 'write', 'delete']):
        require_scene(scene)
        try:
            async with write_lock:
                return await finish_role_write(knowledge_state, scene, item, action)
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.post('/api/host/scenes/{scene}/persona-knowledge/document')
    async def knowledge_create(scene: str, item: KnowledgeChange, _: str = Depends(user)):
        return await write_knowledge(scene, item, 'create')

    @app.put('/api/host/scenes/{scene}/persona-knowledge/document')
    async def knowledge_write(scene: str, item: KnowledgeChange, _: str = Depends(user)):
        return await write_knowledge(scene, item, 'write')

    @app.delete('/api/host/scenes/{scene}/persona-knowledge/document')
    async def knowledge_delete(scene: str, item: KnowledgeFilename, _: str = Depends(user)):
        return await write_knowledge(scene, item, 'delete')

    @app.post("/api/host/scenes/{scene}/persona-examples")
    async def add_example(scene: str, item: ExpressionExample, _: str = Depends(user)):
        require_scene(scene)
        expression = LearningStore(runtime.store).expression(scene, item.expression_id)
        if expression is None:
            raise HTTPException(404, "当前场景没有这条表达候选")
        if expression["status"] != "adopted":
            raise HTTPException(409, "只有已采用的表达可以转成角色样例")
        example = {"context": item.context, "line": item.line, **({"tags": item.tags} if item.tags else {})}

        def write() -> dict:
            saved = load_host_config(root)
            if scene not in saved.scenes:
                raise ValueError("此场景已从保存配置移除，不能追加角色样例")
            path = saved.scenes[scene].persona
            require_persona_target(path, item.directory)
            content = append_example(read_persona_files(path)["examples.yaml"], example)
            return {**files_state(scene, ("examples.yaml", content, item.directory)), "appended": example}

        try:
            async with write_lock:
                return await finish_role_write(write)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error

    @app.get("/api/host/scenes/{scene}/persona-stickers")
    async def stickers(scene: str, response: Response, _: str = Depends(user)):
        require_scene(scene)
        response.headers["Cache-Control"] = "no-store"
        persona = runtime.chats[scene].persona
        uses = runtime.store.sticker_usage(scene, persona.id)
        return {"scene": scene, "persona": {"id": persona.id, "name": persona.name},
                "stickers": [{"file": sticker.file, "description": sticker.description,
                              "emotions": sticker.emotions, "tags": sticker.tags,
                              "mime_type": sticker.mime_type, "width": sticker.width,
                              "height": sticker.height, "animated": sticker.animated,
                              "bytes": len(sticker.data), "confirmed_uses": uses.get(sticker.file, 0)}
                             for sticker in persona.stickers.values()]}

    @app.get("/api/host/scenes/{scene}/persona-stickers/image")
    async def sticker_image(scene: str, file: str, _: str = Depends(user)):
        require_scene(scene)
        sticker = runtime.chats[scene].persona.stickers.get(file)
        if sticker is None:
            raise HTTPException(404, "当前运行角色没有这张表情",
                                headers={"Cache-Control": "no-store"})
        return Response(sticker.data, media_type=sticker.mime_type,
                        headers={"Cache-Control": "no-store"})

    def profile_state(scene: str, change: PersonaProfileChange | None = None) -> dict:
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError('此场景已从保存配置移除；当前运行角色保留到重启，不再编辑其文件')
        path = config.scenes[scene].persona
        files = read_persona_files(path)
        if change is not None:
            require_persona_target(path, change.directory)
            files = profile_files(files, change.profile)
            validate_dependencies(config, path, parse_persona_files(path, files))
            for filename, content in files.items():
                descriptor, name = tempfile.mkstemp(prefix='.persona-', dir=path)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                        stream.write(content)
                    temporary.replace(path / filename)
                finally:
                    temporary.unlink(missing_ok=True)
        persona = parse_persona_files(path, files)
        return {'directory': str(path), 'id': persona.id,
                'profile': PersonaProfile.model_validate(persona.model_dump(include={*PROFILE_METADATA, 'voice', 'boundaries', 'examples'})).model_dump(),
                'affected_scenes': [key for key, value in config.scenes.items() if value.persona == path]}

    @app.get('/api/host/scenes/{scene}/persona-profile')
    async def profile(scene: str, _: str = Depends(user)):
        require_scene(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(profile_state, scene)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.put('/api/host/scenes/{scene}/persona-profile')
    async def save_profile(scene: str, change: PersonaProfileChange, _: str = Depends(user)):
        require_scene(scene)
        try:
            async with write_lock:
                return await finish_role_write(profile_state, scene, change)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    def draft_files(scene: str, change: PersonaProfileChange) -> dict:
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError('此场景已从保存配置移除，不能用它试聊角色草稿')
        path = config.scenes[scene].persona
        require_persona_target(path, change.directory)
        files = profile_files(read_persona_files(path), change.profile)
        parse_persona_files(path, files)
        return {'directory': str(path), 'files': files}

    @app.post('/api/host/scenes/{scene}/persona-profile/draft')
    async def profile_draft(scene: str, change: PersonaProfileChange, _: str = Depends(user)):
        """The four role files the form would write, for a draft test chat; nothing is saved."""
        require_scene(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(draft_files, scene, change)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f'{type(error).__name__}: {error}') from error

    @app.get("/api/host/scenes/{scene}/persona-files")
    async def files(scene: str, _: str = Depends(user)):
        require_scene(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(files_state, scene)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error

    @app.put("/api/host/scenes/{scene}/persona-files/{filename}")
    async def save(scene: str, filename: PersonaFilename, item: PersonaFileChange,
                   _: str = Depends(user)):
        require_scene(scene)
        try:
            async with write_lock:
                return await finish_role_write(files_state, scene, (filename, item.content, item.directory))
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
