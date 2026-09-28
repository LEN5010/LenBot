"""Edit actual configured role files without replacing the running role."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
import yaml

from .chat import build_tools
from .config import STRICT, load_host_config
from .learning_store import LearningStore
from .network import NetworkRuntime
from .skills import load_catalog, select_skills
from .persona import parse_persona_files, read_persona_files


PersonaFilename = Literal["persona.yaml", "voice.md", "boundaries.md", "examples.yaml"]


class PersonaFileChange(BaseModel):
    model_config = STRICT
    content: str


class ExpressionExample(BaseModel):
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

    def files_state(scene: str, edit: tuple[str, str] | None = None) -> dict:
        config = load_host_config(root)
        if scene not in config.scenes:
            raise ValueError("此场景已从保存配置移除；当前运行角色保留到重启，不再编辑其文件")
        path = config.scenes[scene].persona
        files = read_persona_files(path)
        if edit is not None:
            files[edit[0]] = edit[1]
        candidate = parse_persona_files(path, files)
        affected = [key for key, value in config.scenes.items() if value.persona == path]
        for key in affected:
            build_tools(config.scene_config(key), candidate, platform=config.delivery == "onebot")
            if config.worker is not None and config.worker.skills_directory is not None:
                select_skills(load_catalog(config.worker.skills_directory, key,
                                           public_browser=config.worker.public_browser), candidate.skills)
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
        return {"saved": files, "running": running,
                "restart_required": candidate.model_dump() != running or stickers_restart_required
                                    or candidate.knowledge != running_persona.knowledge,
                "stickers_restart_required": stickers_restart_required,
                "affected_scenes": affected}

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
            content = append_example(read_persona_files(path)["examples.yaml"], example)
            return {**files_state(scene, ("examples.yaml", content)), "appended": example}

        try:
            async with write_lock:
                return await asyncio.to_thread(write)
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
                return await asyncio.to_thread(files_state, scene, (filename, item.content))
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
