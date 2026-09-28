"""Edit actual configured role files without replacing the running role."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from .chat import build_tools
from .config import STRICT, load_host_config
from .network import NetworkRuntime
from .skills import load_catalog, select_skills
from .persona import parse_persona_files, read_persona_files


PersonaFilename = Literal["persona.yaml", "voice.md", "boundaries.md", "examples.yaml"]


class PersonaFileChange(BaseModel):
    model_config = STRICT
    content: str


def register_host_persona(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                          user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def require_scene(scene: str) -> None:
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")

    def files_state(scene: str, edit: tuple[str, str] | None = None) -> dict:
        config = load_host_config(root)
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
                "restart_required": candidate.model_dump() != running or stickers_restart_required,
                "stickers_restart_required": stickers_restart_required,
                "affected_scenes": affected}

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
