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

from ...chat.tools import build_tools, tool_catalog, tool_unavailable_reasons
from ...configuration.types import STRICT
from ...config import load_host_config
from ...tools.discovery import DEFERRED_NAMES
from ...runtime.network import NetworkRuntime
from ...persona.profile import load_persona, select_examples
from ...tools.skills import Skill, select_skills
from ...plugins.manifest import scene_skill_catalog


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


class RoleSkills(BaseModel):
    model_config = STRICT
    skills: Literal["all"] | list[str]

    @field_validator("skills")
    @classmethod
    def distinct_names(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and (any(not name.strip() for name in value)
                                      or len(value) != len(set(value))):
            raise ValueError("技能名称不能为空或重复")
        return value


def skill_info(skill: Skill) -> dict:
    return {"name": skill.name, "description": skill.description, "source": skill.source,
            "path": skill.container_path + "/SKILL.md",
            "model_invocation": not skill.disable_model_invocation}


def register_host_capabilities(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                               user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def chat_for(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return runtime.chats[scene]

    def role_state(scene: str) -> dict:
        chat = chat_for(scene)
        saved = load_host_config(root)
        if scene not in saved.scenes:
            raise ValueError("此场景已从保存配置移除，不能读取或编辑保存的角色许可")
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
                "registered": name in chat.toolset.allowed_tool_names,
                "discovered": name in chat.toolset.discovered_tools,
                "deferred": name in DEFERRED_NAMES,
                "reasons": tool_unavailable_reasons(chat.config, chat.persona, name),
            })
        for tool in (([] if runtime.plugins is None else runtime.plugins.tools_for(scene))
                     + ([] if runtime.mcp is None else runtime.mcp.tools_for(scene))):
            allowed = chat.persona.tools == "all" or tool.name in chat.persona.tools
            tools.append({
                "name": tool.name, "description": tool.description, "source": tool.source,
                "allowed": allowed, "registered": tool.name in chat.toolset.allowed_tool_names,
                "discovered": tool.name in chat.toolset.discovered_tools, "deferred": True,
                "reasons": [] if allowed else ["角色没有允许这个工具"],
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
                         "worker_mcp": chat.tasks is not None and chat.tasks.settings.mcp,
                         "skills": (chat.tasks is not None
                                    and chat.tasks.settings.skills_directory is not None),
                         "public_browser": (chat.tasks is not None
                                            and chat.tasks.settings.public_browser),
                         "account_browser": (chat.tasks is not None and chat.tasks.browser is not None
                                             and chat.tasks.browser.settings.browser_instance_id is not None),
                         "public_network": chat.tasks is not None and chat.tasks.settings.egress.enabled,
                         "file_upload": (chat.config.delivery == "onebot" and chat.tasks is not None
                                         and chat.config.onebot.upload_visible_root is not None),
                         "schedules": chat.config.schedules.enabled},
            "not_implemented": [],
        }

    def skills_state(scene: str) -> dict:
        chat = chat_for(scene)
        saved = load_host_config(root)
        if scene not in saved.scenes:
            raise ValueError("此场景已从保存配置移除，不能读取或编辑保存的角色许可")
        path = saved.scenes[scene].persona
        persona = load_persona(path)
        directory = None if saved.worker is None else saved.worker.skills_directory
        catalog = scene_skill_catalog(saved, scene)
        selected = select_skills(catalog, persona.skills) if saved.worker is not None else ()
        running_directory = None if chat.tasks is None else chat.tasks.settings.skills_directory
        return {
            "scene": scene, "directory": None if directory is None else str(directory),
            "running_directory": None if running_directory is None else str(running_directory),
            "role_skills": {"saved": persona.skills, "running": chat.persona.skills,
                            "restart_required": persona.skills != chat.persona.skills,
                            "affected_scenes": [key for key, value in saved.scenes.items()
                                                if value.persona == path]},
            "catalog": [{**skill_info(skill), "selected": skill in selected} for skill in catalog],
            "running": [skill_info(skill) for skill in chat.skills],
        }

    @app.get("/api/host/skills")
    async def skills(scene: str, _: str = Depends(user)):
        chat_for(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(skills_state, scene)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error

    @app.get("/api/host/scenes/{scene}/persona")
    async def persona(scene: str, _: str = Depends(user)):
        loaded = chat_for(scene).persona
        return {
            "scene": scene, "persona": loaded.model_dump(),
            "selected_examples": [item.model_dump() for item in select_examples(loaded)],
            "knowledge": [{"filename": name, "tags": list(item.tags), "characters": len(item.content)}
                          for name, item in sorted(loaded.knowledge.items())],
        }

    def save_role(scene: str, field: Literal["tools", "skills"], value: str | list[str]) -> dict:
        saved = load_host_config(root)
        if scene not in saved.scenes:
            raise ValueError("此场景已从保存配置移除，不能读取或编辑保存的角色许可")
        path = saved.scenes[scene].persona
        persona = load_persona(path).model_copy(update={field: value})
        affected = [key for key, value in saved.scenes.items() if value.persona == path]
        for key in affected:
            build_tools(saved.scene_config(key), persona, platform=saved.delivery == "onebot")
            if saved.worker is not None:
                select_skills(scene_skill_catalog(saved, key), persona.skills)
        metadata = persona.model_dump(exclude={"voice", "boundaries", "examples", "knowledge"})
        descriptor, name = tempfile.mkstemp(prefix=".persona-", suffix=".yaml", dir=path)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                yaml.safe_dump(metadata, stream, allow_unicode=True, sort_keys=False)
            temporary.replace(path / "persona.yaml")
        finally:
            temporary.unlink(missing_ok=True)
        current = getattr(chat_for(scene).persona, field)
        return {"saved": value, "running": current,
                "restart_required": value != current, "affected_scenes": affected}

    @app.put("/api/host/scenes/{scene}/role-tools")
    async def save(scene: str, changes: RoleTools, _: str = Depends(user)):
        chat_for(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(save_role, scene, "tools", changes.tools)
        except (ValueError, OSError) as error:
            logger.exception("保存角色工具设置失败：%s", error)
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error

    @app.put("/api/host/scenes/{scene}/role-skills")
    async def save_skills(scene: str, changes: RoleSkills, _: str = Depends(user)):
        chat_for(scene)
        try:
            async with write_lock:
                return await asyncio.to_thread(save_role, scene, "skills", changes.skills)
        except (ValueError, OSError) as error:
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
