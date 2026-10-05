"""Validated root-file settings for the running multi-scene host panel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from ...configuration.types import STRICT
from ...configuration.chat import (
    Compaction,
    ImageSettings,
    Attention,
    Proactive,
    ScenePersona,
    TextDelivery,
    WebReadSettings,
)
from ...config import HostConfig
from ...configuration.editing import _read_saved, restart_summary, save_config
from ...configuration.learning import LearningSettings
from ...configuration.models import Roles
from ...persona.profile import Persona
from ...models.asr import AudioSettings
from ...runtime.operations import LoggingSettings
from ...models.limits import ResourceLimits
from ...runtime.retention import RetentionSettings
from ...models.pricing import ModelPrice
from ...tools.web_search import WebSearchSettings
from ...memory.service import RecallSettings
from ...memory.embeddings import EmbeddingBinding
from len_bot.web.auth import hash_password


class RetentionChange(BaseModel):
    model_config = STRICT
    retention: RetentionSettings | None


class ProcessingChange(BaseModel):
    model_config = STRICT
    compaction: Compaction
    images: ImageSettings
    audio: AudioSettings
    logging: LoggingSettings | None


class ProviderChange(BaseModel):
    model_config = STRICT

    api: Literal["openai-chat", "openai-audio", "openai-embeddings"]
    base_url: str
    api_key: str | None = Field(default=None, repr=False)


class ModelsChange(BaseModel):
    model_config = STRICT

    providers: dict[str, ProviderChange]
    roles: Roles
    prices: dict[str, dict[str, ModelPrice]]


class SceneBindingChange(BaseModel):
    model_config = STRICT
    persona: str = Field(min_length=1)


class ScheduleSwitches(BaseModel):
    """Reminder switches edited with the scene; who may use them is edited on the permissions page."""
    model_config = STRICT
    enabled: bool
    max_pending: int
    autonomous: bool


class SceneChange(ScenePersona):
    timezone: str | None
    voice_mode: Literal["direct"]
    attention: Attention
    schedules: ScheduleSwitches
    proactive: Proactive | None
    transcribe_audio: bool


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
    summaries: bool


class SceneCreate(SceneBindingChange):
    scene: str


class MemoryChange(BaseModel):
    model_config = STRICT
    memory: LocalMemoryChange | None


class WorkerChange(BaseModel):
    model_config = STRICT
    # The root loader resolves its paths and validates the whole candidate once.
    worker: dict | None


class TaskSwitches(BaseModel):
    """Task switches and limits edited with the scene; roles and lists live on the permissions page."""
    model_config = STRICT
    enabled: bool
    max_running: int
    max_daily_tasks: int
    egress_max_task_bytes: int | None
    egress_max_daily_bytes: int | None
    egress_bytes_per_second: int | None


class TaskSceneChange(BaseModel):
    model_config = STRICT
    tasks: TaskSwitches


class LearningSceneChange(BaseModel):
    model_config = STRICT
    learning: LearningSettings | None


def _memory_settings(config: HostConfig) -> dict | None:
    memory = config.memory
    if memory is None:
        return None
    return memory.model_dump(mode="json")


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
    owner_qq: str | None
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
        "limits": config.limits.model_dump(mode="json"),
        "retention": None if config.retention is None else config.retention.model_dump(mode="json"),
        "processing": {key: (None if getattr(config, key) is None else getattr(config, key).model_dump(mode="json"))
                       for key in ("compaction", "images", "audio", "logging")},
        "connection": {
            "bot_qq": config.bot_qq,
            "owner_qq": config.owner_qq,
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
                "persona": str(settings.persona),
                "timezone": settings.timezone,
                "voice_mode": settings.voice_mode,
                "attention": settings.attention.model_dump(mode="json"),
                "schedules": settings.schedules.model_dump(mode="json"),
                "proactive": None if settings.proactive is None else settings.proactive.model_dump(mode="json"),
                "transcribe_audio": settings.transcribe_audio,
                "tasks": settings.tasks.model_dump(mode="json"),
                "learning": None if settings.learning is None else settings.learning.model_dump(mode="json"),
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
                for name in ("bot_qq", "owner_qq", "onebot", "timezone", "delivery", "max_steps",
                             "turn_timeout_seconds", "max_model_requests", "text_delivery")
            ),
            "limits": running.limits != saved.limits,
            "retention": running.retention != saved.retention,
            "processing": current["processing"] != recorded["processing"],
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


def _validation_detail(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
        for item in error.errors(include_input=False)
    )


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
                           personas: Callable[[], dict[str, Persona]],
                           user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    def learning_snapshot(scene: str, snapshot: dict) -> dict:
        if scene not in snapshot["saved"]["scenes"]:
            raise HTTPException(404, "此场景已从保存配置移除，运行学习配置保留到重启")
        current = snapshot["running"]["scenes"][scene]["learning"]
        recorded = snapshot["saved"]["scenes"][scene]["learning"]
        return {"scene": scene, "running": current, "saved": recorded,
                "restart_required": current != recorded}

    async def save(edit: Callable[[dict, HostConfig], None]) -> dict:
        async with write_lock:
            try:
                candidate = await asyncio.to_thread(save_config, root, running, edit)
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

    @app.get("/api/host/pending-restart")
    async def pending_restart(_: str = Depends(user)):
        async with write_lock:
            try:
                return await asyncio.to_thread(restart_summary, root, running, personas())
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error

    @app.put("/api/host/settings/retention")
    async def retention(request: Request, _: str = Depends(user)):
        change = await _body(request, RetentionChange)
        return await save(lambda source, saved: source.update(change.model_dump(mode="json")))

    @app.put("/api/host/settings/limits")
    async def limits(request: Request, _: str = Depends(user)):
        change = await _body(request, ResourceLimits)
        return await save(lambda source, saved: source.update(limits=change.model_dump(mode="json")))

    @app.put("/api/host/settings/processing")
    async def processing(request: Request, _: str = Depends(user)):
        try:
            change = ProcessingChange.model_validate_json(await request.body())
        except ValidationError as error:
            raise HTTPException(422, _validation_detail(error)) from error
        return await save(lambda source, saved: source.update(change.model_dump(mode="json")))

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

    @app.post("/api/host/settings/scenes")
    async def create_scene(request: Request, _: str = Depends(user)):
        change: SceneCreate = await _body(request, SceneCreate)
        def edit(source: dict, saved: HostConfig) -> None:
            if change.scene in saved.scenes:
                raise ValueError(f"场景已存在：{change.scene}")
            source["scenes"][change.scene] = {"persona": change.persona, "attention": {"only_direct": True}}
        return await save(edit)

    @app.put("/api/host/settings/scenes/{scene}/persona")
    async def bind_scene_persona(scene: str, request: Request, _: str = Depends(user)):
        change: SceneBindingChange = await _body(request, SceneBindingChange)
        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"保存配置没有场景：{scene}")
            source["scenes"][scene]["persona"] = change.persona
        return await save(edit)

    @app.delete("/api/host/settings/scenes/{scene}")
    async def delete_scene(scene: str, _: str = Depends(user)):
        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"保存配置没有场景：{scene}")
            del source["scenes"][scene]
        return await save(edit)

    @app.put("/api/host/settings/scenes/{scene}")
    async def put_scene(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change: SceneChange = await _body(request, SceneChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"根配置已不包含场景 {scene!r}")
            values = change.model_dump(mode="json")
            local = source["scenes"][scene]
            local.setdefault("schedules", {}).update(values.pop("schedules"))
            local.update(values)

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
            source["scenes"][scene].setdefault("tasks", {}).update(change.tasks.model_dump(mode="json"))

        return await save(edit)

    @app.get("/api/host/settings/scenes/{scene}/learning")
    async def get_learning(scene: str, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        async with write_lock:
            try:
                saved = await asyncio.to_thread(_read_saved, root)
            except (ValueError, OSError) as error:
                raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                    f"{type(error).__name__}: {error}") from error
            return learning_snapshot(scene, _snapshot(running, saved))

    @app.put("/api/host/settings/scenes/{scene}/learning")
    async def put_learning(scene: str, request: Request, _: str = Depends(user)):
        if scene not in running.scenes:
            raise HTTPException(404, "当前宿主未配置这一场景")
        change: LearningSceneChange = await _body(request, LearningSceneChange)

        def edit(source: dict, saved: HostConfig) -> None:
            if scene not in saved.scenes:
                raise ValueError(f"根配置已不包含场景 {scene!r}")
            source["scenes"][scene]["learning"] = (
                None if change.learning is None else change.learning.model_dump(mode="json")
            )

        return learning_snapshot(scene, await save(edit))

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
                owner_qq=change.owner_qq,
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
