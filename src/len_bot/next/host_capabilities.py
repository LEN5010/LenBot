"""Expose the running tool set and save the configured role's tool permissions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import os
from pathlib import Path
import tempfile
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, field_validator
import yaml

from .chat import build_tools, tool_catalog, tool_unavailable_reasons
from .config import STRICT, load_host_config
from .discovery import DEFERRED_NAMES
from .network import NetworkRuntime
from .persona import load_persona, select_examples


logger = logging.getLogger(__name__)


class RoleTools(BaseModel):
    model_config = STRICT
    tools: Literal["all"] | list[str]

    @field_validator("tools")
    @classmethod
    def distinct_names(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list):
            if any(not name.strip() for name in value):
                raise ValueError("工具名称不能为空")
            if len(value) != len(set(value)):
                raise ValueError("工具名称不能重复")
        return value


def register_host_capabilities(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                               user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def chat_for(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return runtime.chats[scene]

    def role_state(scene: str) -> dict:
        chat = chat_for(scene)
        saved = load_host_config(root)
        path = saved.scenes[scene].persona
        persona = load_persona(path)
        return {
            "saved": persona.tools, "running": chat.persona.tools,
            "restart_required": persona.tools != chat.persona.tools,
            "affected_scenes": [key for key, value in saved.scenes.items() if value.persona == path],
        }

    @app.get("/api/host/capabilities")
    async def capabilities(scene: str, _: str = Depends(user)):
        chat = chat_for(scene)
        try:
            async with write_lock:
                roles = await asyncio.to_thread(role_state, scene)
        except (ValueError, OSError) as error:
            logger.exception("读取角色工具设置失败：%s", error)
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
        tools = []
        for tool in tool_catalog(platform=chat.config.delivery == "onebot"):
            function = tool["function"]
            name = function["name"]
            tools.append({
                "name": name, "description": function["description"],
                "allowed": chat.persona.tools == "all" or name in chat.persona.tools,
                "registered": name in chat.allowed_tool_names,
                "discovered": name in chat.discovered_tools,
                "deferred": name in DEFERRED_NAMES,
                "reasons": tool_unavailable_reasons(chat.config, chat.persona, name),
            })
        return {
            "scene": scene,
            "scenes": [{"scene": key, "persona": {"id": value.persona.id, "name": value.persona.name}}
                       for key, value in runtime.chats.items()],
            "persona": {"id": chat.persona.id, "name": chat.persona.name,
                        "tools": chat.persona.tools, "skills": chat.persona.skills},
            "role_tools": roles, "tools": tools,
            "services": {"web_read": chat.config.web_read is not None,
                         "web_search": chat.config.web_search is not None,
                         "memory": chat.memory is not None,
                         "vision": chat.config.models.roles.vision is not None,
                         "worker": chat.tasks is not None,
                         "file_upload": (chat.config.delivery == "onebot" and chat.tasks is not None
                                         and chat.config.onebot.upload_visible_root is not None),
                         "schedules": chat.config.schedules.enabled},
            "not_implemented": [
                {"name": "public_network", "description": "任务容器的公共联网尚未接入。"},
                {"name": "skills", "description": "角色技能声明尚未接入执行环境。"},
                {"name": "plugins / MCP", "description": "当前宿主尚未装载插件和 MCP 服务。"},
            ],
        }

    @app.get("/api/host/scenes/{scene}/persona")
    async def persona(scene: str, _: str = Depends(user)):
        loaded = chat_for(scene).persona
        return {
            "scene": scene, "persona": loaded.model_dump(),
            "selected_examples": [item.model_dump() for item in select_examples(loaded)],
            "knowledge": [{"filename": name, "tags": list(item.tags), "characters": len(item.content)}
                          for name, item in sorted(loaded.knowledge.items())],
        }

    def save_role(scene: str, changes: RoleTools) -> dict:
        saved = load_host_config(root)
        path = saved.scenes[scene].persona
        persona = load_persona(path).model_copy(update={"tools": changes.tools})
        affected = [key for key, value in saved.scenes.items() if value.persona == path]
        for key in affected:
            build_tools(saved.scene_config(key), persona, platform=saved.delivery == "onebot")
        metadata = persona.model_dump(exclude={"voice", "boundaries", "examples", "knowledge"})
        descriptor, name = tempfile.mkstemp(prefix=".persona-", suffix=".yaml", dir=path)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                yaml.safe_dump(metadata, stream, allow_unicode=True, sort_keys=False)
            temporary.replace(path / "persona.yaml")
        finally:
            temporary.unlink(missing_ok=True)
        current = chat_for(scene).persona.tools
        return {"saved": changes.tools, "running": current,
                "restart_required": changes.tools != current, "affected_scenes": affected}

    @app.put("/api/host/scenes/{scene}/role-tools")
    async def save(scene: str, changes: RoleTools, _: str = Depends(user)):
        chat_for(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(save_role, scene, changes)
        except (ValueError, OSError) as error:
            logger.exception("保存角色工具设置失败：%s", error)
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
