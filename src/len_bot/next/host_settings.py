"""Validated root-file settings for the running multi-scene host panel."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .chat import build_tools
from .config import (
    STRICT, Attention, HostConfig, Roles, ScenePersona, ScheduleSettings,
    TextDelivery, WebReadSettings, _load_host_source, _read_root,
)
from .persona import load_persona
from .pricing import ModelPrice
from .web_search import WebSearchSettings
from .memory import RecallSettings, LocalMemoryConfig, OpenVikingMemoryConfig
from .memory_embeddings import EmbeddingBinding
from .tasks_config import TaskSettings
from len_bot.web.auth import hash_password


class ProviderChange(BaseModel):
    model_config = STRICT

    api: Literal["openai-chat"]
    base_url: str
    api_key: str | None = Field(default=None, repr=False)


class ModelsChange(BaseModel):
    model_config = STRICT

    providers: dict[str, ProviderChange]
    roles: Roles
    prices: dict[str, dict[str, ModelPrice]]


class SceneChange(ScenePersona):
    voice_mode: Literal["voice", "direct"]
    attention: Attention
    schedules: ScheduleSettings


class WebReadChange(BaseModel):
    model_config = STRICT

    web_read: WebReadSettings | None


class WebSearchChange(BaseModel):
    model_config = STRICT

    web_search: WebSearchSettings | None


class LocalMemoryChangeSettings(BaseModel):
    model_config = STRICT
    directory: str = Field(min_length=1)
    embedding: EmbeddingBinding | None = None


class LocalMemoryChange(RecallSettings):
    backend: Literal["local"]
    local: LocalMemoryChangeSettings


class MemorySceneIdentityChange(BaseModel):
    model_config = STRICT
    user_id: str
    api_key: str | None = Field(default=None, repr=False)


class OpenVikingChangeSettings(BaseModel):
    model_config = STRICT
    base_url: str
    account_id: str
    timeout_seconds: float
    public_root: str | None
    scenes: dict[str, MemorySceneIdentityChange]


class OpenVikingMemoryChange(RecallSettings):
    backend: Literal["openviking"]
    openviking: OpenVikingChangeSettings


class MemoryChange(BaseModel):
    model_config = STRICT
    memory: Annotated[LocalMemoryChange | OpenVikingMemoryChange, Field(discriminator="backend")] | None


class WorkerChange(BaseModel):
    model_config = STRICT
    # The root loader resolves its paths and validates the whole candidate once.
    worker: dict | None


class TaskSceneChange(BaseModel):
    model_config = STRICT
    tasks: TaskSettings


def _memory_settings(config: HostConfig) -> dict | None:
    memory = config.memory
    if memory is None:
        return None
    if isinstance(memory, LocalMemoryConfig):
        return memory.model_dump(mode="json")
    result = memory.model_dump(mode="json", exclude={"openviking": {"scenes"}})
    result["openviking"]["scenes"] = {
        scene: {"user_id": identity.user_id, "api_key_configured": bool(identity.api_key)}
        for scene, identity in memory.openviking.scenes.items()
    }
    return result


class OneBotPublic(BaseModel):
    model_config = STRICT

    action_transport: Literal["websocket", "http"]
    http_url: str | None
    request_timeout_seconds: float
    ping_interval_seconds: float
    ping_timeout_seconds: float
    max_frame_bytes: int
    upload_visible_root: str | None = None


class ForwardPublic(OneBotPublic):
    mode: Literal["forward_ws"]
    ws_url: str


class ReversePublic(OneBotPublic):
    mode: Literal["reverse_ws"]
    listen_host: str
    listen_port: int


class ConnectionChange(BaseModel):
    model_config = STRICT

    onebot: Annotated[ForwardPublic | ReversePublic, Field(discriminator="mode")]
    access_token: str | None = Field(default=None, repr=False)
    timezone: str
    delivery: Literal["simulated", "onebot"]
    max_steps: int
    turn_timeout_seconds: float
    max_model_requests: int
    text_delivery: TextDelivery


class PanelChange(BaseModel):
    model_config = STRICT

    host: str
    port: int
    username: str
    cookie_secure: bool
    password: str | None = Field(default=None, repr=False)


def _project(config: HostConfig) -> dict:
    models = config.models
    onebot = config.onebot.model_dump(mode="json", exclude={"access_token"})
    onebot["access_token_configured"] = bool(config.onebot.access_token)
    return {
        "connection": {
            "bot_qq": config.bot_qq,
            "onebot": onebot,
            "timezone": config.timezone,
            "delivery": config.delivery,
            "max_steps": config.max_steps,
            "turn_timeout_seconds": config.turn_timeout_seconds,
            "max_model_requests": config.max_model_requests,
            "text_delivery": config.text_delivery.model_dump(mode="json"),
        },
        "panel": None if config.panel is None else {
            "host": config.panel.host,
            "port": config.panel.port,
            "username": config.panel.username,
            "cookie_secure": config.panel.cookie_secure,
        },
        "models": {
            "providers": {
                alias: {"api": provider.api, "base_url": provider.base_url,
                        "api_key_configured": bool(provider.api_key)}
                for alias, provider in models.providers.items()
            },
            "roles": models.roles.model_dump(mode="json"),
            "prices": {
                provider: {name: price.model_dump(mode="json") for name, price in entries.items()}
                for provider, entries in models.prices.items()
            },
        },
        "scenes": {
            scene: {
                "voice_mode": settings.voice_mode,
                "attention": settings.attention.model_dump(mode="json"),
                "schedules": settings.schedules.model_dump(mode="json"),
                "tasks": settings.tasks.model_dump(mode="json"),
                "scene_persona": {
                    "persona_aliases": settings.persona_aliases,
                    "relationships": settings.relationships,
                    "behavior_addendum": settings.behavior_addendum,
                },
            }
            for scene, settings in config.scenes.items()
        },
        "web_read": None if config.web_read is None else config.web_read.model_dump(mode="json"),
        "web_search": None if config.web_search is None else config.web_search.model_dump(mode="json"),
        "memory": _memory_settings(config),
        "worker": None if config.worker is None else config.worker.model_dump(mode="json"),
    }


def _snapshot(running: HostConfig, saved: HostConfig) -> dict:
    current, recorded = _project(running), _project(saved)
    return {
        "running": current,
        "saved": recorded,
        "restart_required": {
            "connection": any(
                getattr(running, name) != getattr(saved, name)
                for name in ("bot_qq", "onebot", "timezone", "delivery", "max_steps",
                             "turn_timeout_seconds", "max_model_requests", "text_delivery")
            ),
            "panel": running.panel != saved.panel,
            "models": running.models != saved.models,
            "scenes": {
                scene: current["scenes"].get(scene) != recorded["scenes"].get(scene)
                for scene in sorted(current["scenes"].keys() | recorded["scenes"].keys())
            },
            "web_read": running.web_read != saved.web_read,
            "web_search": running.web_search != saved.web_search,
            "memory": running.memory != saved.memory,
            "worker": running.worker != saved.worker,
        },
    }


def _prepare(root: Path, edit: Callable[[dict, HostConfig], None]
             ) -> tuple[Path, Path, HostConfig]:
    path, original = _read_root(root)
    saved = _load_host_source(path, copy.deepcopy(original))
    edit(original, saved)
    candidate = _load_host_source(path, copy.deepcopy(original))
    personas = {path: load_persona(path)
                for path in dict.fromkeys(settings.persona for settings in candidate.scenes.values())}
    for scene, settings in candidate.scenes.items():
        build_tools(candidate.scene_config(scene), personas[settings.persona],
                    platform=candidate.delivery == "onebot")

    descriptor, name = tempfile.mkstemp(prefix=".lenbot-config-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(original, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path, temporary, candidate


def _mind_binding(config: HostConfig) -> tuple[str, str, str]:
    binding = config.models.roles.mind
    provider = config.models.providers[binding.provider]
    return provider.api, provider.base_url, binding.model


def _validation_detail(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
        for item in error.errors(include_input=False)
    )


def _read_saved(root: Path) -> HostConfig:
    path, source = _read_root(root)
    return _load_host_source(path, source)


async def _body(request: Request, kind: type[BaseModel]) -> BaseModel:
    try:
        raw = await request.json()
    except ValueError as error:
        raise HTTPException(422, f"Invalid JSON: {error}") from error
    try:
        return kind.model_validate(raw)
    except ValidationError as error:
        raise HTTPException(422, _validation_detail(error)) from error


def register_host_settings(app: FastAPI, *, root: Path, running: HostConfig,
                           user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    async def save(edit: Callable[[dict, HostConfig], None]) -> dict:
        async with write_lock:
            try:
                path, temporary, candidate = await asyncio.to_thread(_prepare, root, edit)
                try:
                    if _mind_binding(candidate) != _mind_binding(running):
                        raise ValueError(
                            "运行中不能保存大脑协议、地址或模型变更；即使此刻没有历史，"
                            "当前进程仍可能写入旧绑定。请先停机，再显式转换可移植历史。根配置未保存"
                        )
                    if running.worker is not None and candidate.worker is not None:
                        if any(getattr(running.worker, key) != getattr(candidate.worker, key)
                               for key in ("docker_host", "workspace_root", "runtime_root")):
                            raise ValueError(
                                "运行中不能保存任务 Docker 地址、工作区或运行目录的迁移；"
                                "先停机清理任务容器，再搬迁原文件和修改根配置。根配置未保存"
                            )
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error
            return _snapshot(running, candidate)

    @app.get("/api/host/settings")
    async def get_settings(_: str = Depends(user)):
        async with write_lock:
            try:
                saved = await asyncio.to_thread(_read_saved, root)
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error
            return _snapshot(running, saved)

    @app.put("/api/host/settings/models")
    async def put_models(request: Request, _: str = Depends(user)):
        change: ModelsChange = await _body(request, ModelsChange)

        def edit(source: dict, saved: HostConfig) -> None:
            providers = {}
            for alias, provider in change.providers.items():
                if provider.api_key is None:
                    if alias not in saved.models.providers:
                        raise ValueError(f"models.providers.{alias}.api_key is required for a new provider")
                    key = saved.models.providers[alias].api_key
                else:
                    key = provider.api_key
                providers[alias] = {"api": provider.api, "base_url": provider.base_url, "api_key": key}
            source["models"] = {
                "providers": providers,
                "roles": change.roles.model_dump(mode="json"),
                "prices": {
                    alias: {name: price.model_dump(mode="json") for name, price in entries.items()}
                    for alias, entries in change.prices.items()
                },
            }

        return await save(edit)

    @app.put("/api/host/settings/scenes/{scene}")
    async def put_scene(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change: SceneChange = await _body(request, SceneChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"根配置已不包含场景 {scene!r}")
            source["scenes"][scene].update(change.model_dump(mode="json"))

        return await save(edit)

    @app.put("/api/host/settings/web-read")
    async def put_web_read(request: Request, _: str = Depends(user)):
        change: WebReadChange = await _body(request, WebReadChange)
        return await save(lambda source, _: source.update(web_read=(
            None if change.web_read is None else change.web_read.model_dump(mode="json")
        )))

    @app.put("/api/host/settings/worker")
    async def put_worker(request: Request, _: str = Depends(user)):
        change: WorkerChange = await _body(request, WorkerChange)
        return await save(lambda source, _: source.update(worker=change.worker))

    @app.put("/api/host/settings/scenes/{scene}/tasks")
    async def put_tasks(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change: TaskSceneChange = await _body(request, TaskSceneChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"根配置已不包含场景 {scene!r}")
            source["scenes"][scene]["tasks"] = change.tasks.model_dump(mode="json")

        return await save(edit)

    @app.put("/api/host/settings/web-search")
    async def put_web_search(request: Request, _: str = Depends(user)):
        change: WebSearchChange = await _body(request, WebSearchChange)
        return await save(lambda source, _: source.update(web_search=(
            None if change.web_search is None else change.web_search.model_dump(mode="json")
        )))

    @app.put("/api/host/settings/connection")
    async def put_connection(request: Request, _: str = Depends(user)):
        change: ConnectionChange = await _body(request, ConnectionChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if change.access_token == "":
                raise ValueError("access_token replacement must not be empty")
            onebot = change.onebot.model_dump(mode="json")
            onebot["access_token"] = (
                saved.onebot.access_token if change.access_token is None else change.access_token
            )
            source.update(
                onebot=onebot,
                timezone=change.timezone,
                delivery=change.delivery,
                max_steps=change.max_steps,
                turn_timeout_seconds=change.turn_timeout_seconds,
                max_model_requests=change.max_model_requests,
                text_delivery=change.text_delivery.model_dump(mode="json"),
            )

        return await save(edit)

    @app.put("/api/host/settings/memory")
    async def put_memory(request: Request, _: str = Depends(user)):
        change: MemoryChange = await _body(request, MemoryChange)

        def edit(source: dict, saved: HostConfig) -> None:
            memory = change.memory
            if memory is None:
                source["memory"] = None
                return
            result = memory.model_dump(mode="json")
            if isinstance(memory, OpenVikingMemoryChange):
                previous = saved.memory
                same_service = (isinstance(previous, OpenVikingMemoryConfig)
                                and previous.openviking.base_url.rstrip("/") == memory.openviking.base_url.rstrip("/")
                                and previous.openviking.account_id == memory.openviking.account_id)
                for scene, identity in memory.openviking.scenes.items():
                    if identity.api_key is None:
                        old = previous.openviking.scenes.get(scene) if same_service else None
                        if old is None or old.user_id != identity.user_id:
                            raise ValueError(f"memory.openviking.scenes.{scene}.api_key is required for this identity")
                        result["openviking"]["scenes"][scene]["api_key"] = old.api_key
            source["memory"] = result

        return await save(edit)

    @app.put("/api/host/settings/panel")
    async def put_panel(request: Request, _: str = Depends(user)):
        change: PanelChange = await _body(request, PanelChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if change.password == "":
                raise ValueError("password replacement must not be empty")
            if change.password is None:
                if saved.panel is None:
                    raise ValueError("password is required when panel is not configured")
                password_hash = saved.panel.password_hash
            else:
                password_hash = hash_password(change.password)
            panel = source.get("panel")
            if not isinstance(panel, dict):
                panel = {}
            panel.update(
                host=change.host,
                port=change.port,
                username=change.username,
                cookie_secure=change.cookie_secure,
                password_hash=password_hash,
            )
            source["panel"] = panel

        return await save(edit)
