"""Authenticated memory content management for configured host scenes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .config import STRICT
from .memory_local import LocalMemory
from .network import NetworkRuntime


logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    model_config = STRICT
    scene: str
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


class WriteRequest(BaseModel):
    model_config = STRICT
    scene: str
    path: str = Field(min_length=1)
    scope: Literal["scene", "public"]
    content: str
    reason: str = Field(min_length=1)


class DeleteRequest(BaseModel):
    model_config = STRICT
    scene: str
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    forget: bool


def register_host_memory(app: FastAPI, *, runtime: NetworkRuntime, user) -> None:
    @asynccontextmanager
    async def operation(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")
        if runtime.memory is None:
            raise HTTPException(409, "当前运行配置未启用长期记忆后端")
        try:
            yield runtime.memory
        except Exception as error:
            logger.exception("记忆请求失败，场景 %s：%s", scene, error)
            code = (404 if isinstance(error, FileNotFoundError) else
                    422 if isinstance(error, ValueError) else 500)
            raise HTTPException(code, f"{type(error).__name__}: {error}") from error

    @app.get("/api/host/memory/state")
    async def state(_: str = Depends(user)):
        memory = runtime.memory
        return {
            "enabled": memory is not None,
            "backend": None if memory is None else memory.settings.backend,
            "actions": [] if memory is None else memory.actions,
            "public_writable": memory is not None and isinstance(memory.backend, LocalMemory),
            "public_readable": memory is not None and (isinstance(memory.backend, LocalMemory)
                                or memory.settings.openviking.public_root is not None),
            "auto_recall": memory is not None and memory.settings.auto_recall,
            "recall_budget_chars": None if memory is None else memory.settings.recall_budget_chars,
            "scenes": [{"scene": scene, "persona": {"id": chat.persona.id, "name": chat.persona.name}}
                       for scene, chat in runtime.chats.items()],
        }

    @app.get("/api/host/memory/browse")
    async def browse(scene: str, path: str = "", scope: Literal["scene", "public"] = "scene",
                     offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                     _: str = Depends(user)):
        async with operation(scene) as memory:
            return asdict(await memory.backend.browse(scene, path, scope=scope, offset=offset, limit=limit))

    @app.get("/api/host/memory/read")
    async def read(scene: str, path: str, scope: Literal["scene", "public"] = "scene", _: str = Depends(user)):
        async with operation(scene) as memory:
            return asdict(await memory.backend.read(scene, path, scope=scope))

    @app.get("/api/host/memory/history")
    async def history(scene: str, path: str, _: str = Depends(user)):
        async with operation(scene) as memory:
            return {"changes": await memory.history(scene, path)}

    @app.post("/api/host/memory/search")
    async def search(body: SearchRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return {"hits": await memory.search(body.scene, body.query, body.limit)}

    @app.put("/api/host/memory/file")
    async def write(body: WriteRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return await memory.write(body.scene, body.path, body.content, body.reason, scope=body.scope)

    @app.post("/api/host/memory/delete")
    async def delete(body: DeleteRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return await memory.delete(body.scene, body.path, body.reason, forget=body.forget)

    @app.get("/api/host/memory/ingest")
    async def ingest_state(_: str = Depends(user)):
        ingestor = runtime.ingestor
        return {"enabled": ingestor is not None, "scenes": [] if ingestor is None else [
            {**ingestor.jobs.view(scene), "worker_error": (
                f"{type(ingestor.errors[scene]).__name__}: {ingestor.errors[scene]}"
                if scene in ingestor.errors else None)} for scene in runtime.chats
        ]}

    @app.post("/api/host/memory/ingest/{scene}/{action}")
    async def ingest(scene: str, action: Literal["run", "retry", "refresh"], _: str = Depends(user)):
        async with operation(scene):
            ingestor = runtime.ingestor
            if ingestor is None:
                raise ValueError("当前运行配置未开启记忆自动抽取")
            if action == "retry":
                return {"requested": "retry", "job": ingestor.retry(scene)}
            if action == "refresh":
                ingestor.refresh(scene)
            else:
                ingestor.request(scene)
            return {"requested": action, "state": ingestor.jobs.view(scene)}
