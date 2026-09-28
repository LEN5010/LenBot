"""Authenticated scene-local expression learning records and operator decisions."""

from dataclasses import asdict
import sqlite3
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .config import STRICT
from .learning_store import LearningStore
from .network import NetworkRuntime


class ExpressionChange(BaseModel):
    model_config = STRICT
    situation: str = Field(min_length=1)
    style: str = Field(min_length=1)
    status: Literal["pending", "adopted", "rejected"]

    @field_validator("situation", "style")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("情境和说法不能只包含空白")
        return value


def register_host_learning(app: FastAPI, *, runtime: NetworkRuntime,
                           user: Callable[[Request], str]) -> None:
    learning = LearningStore(runtime.store)

    def service_state(scene: str) -> dict | None:
        return runtime.learning.state(scene) if active(scene) else None

    def chat_for(scene: str):
        chat = runtime.chats.get(scene)
        if chat is None:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return chat

    def active(scene: str) -> bool:
        return (runtime.learning is not None and scene in runtime.learning.scenes
                and chat_for(scene).config.learning is not None)

    def trigger(scene: str, action: Literal["run", "retry"]) -> dict:
        chat_for(scene)
        if not active(scene):
            raise HTTPException(409, "当前运行场景未启用表达学习服务")
        if not runtime.accepting:
            raise HTTPException(409, "宿主正在停止，不再接收学习请求")
        try:
            if action == "run":
                runtime.learning.request(scene)
            else:
                runtime.learning.retry(scene)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        runtime.notify()
        return {"requested": action, "state": service_state(scene)}

    @app.get("/api/host/scenes/{scene}/learning")
    async def state(scene: str, _: str = Depends(user)):
        chat = chat_for(scene)
        latest = learning.latest(scene)
        return {"scene": scene, "enabled": active(scene),
                "settings": None if chat.config.learning is None else
                chat.config.learning.model_dump(mode="json"),
                "cursor": learning.state(scene),
                "latest": latest,
                "expression_counts": learning.counts(scene),
                "service_state": service_state(scene)}

    @app.get("/api/host/scenes/{scene}/learning/batches")
    async def batches(scene: str, limit: int = Query(20, ge=1, le=20),
                      offset: int = Query(0, ge=0), _: str = Depends(user)):
        chat_for(scene)
        return learning.batches(scene, limit=limit, offset=offset)

    @app.get("/api/host/scenes/{scene}/learning/batches/{id}")
    async def batch(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        item = learning.batch(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这一学习批次")
        return item

    @app.post("/api/host/scenes/{scene}/learning/request")
    async def request(scene: str, _: str = Depends(user)):
        return trigger(scene, "run")

    @app.post("/api/host/scenes/{scene}/learning/retry")
    async def retry(scene: str, _: str = Depends(user)):
        return trigger(scene, "retry")

    @app.get("/api/host/scenes/{scene}/learning/expressions")
    async def expressions(scene: str, status: Literal["pending", "adopted", "rejected"] | None = None,
                          limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0),
                          _: str = Depends(user)):
        chat_for(scene)
        return learning.expressions(scene, status=status, limit=limit, offset=offset)

    @app.get("/api/host/scenes/{scene}/learning/expressions/{id}")
    async def expression(scene: str, id: int, _: str = Depends(user)):
        chat = chat_for(scene)
        item = learning.expression(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这条表达候选")
        sources = []
        for record in item["sources"]:
            message = runtime.store.read_message(scene, record)
            sources.append({"record": record, "available": message is not None,
                            "rendered": None if message is None else chat.render(message),
                            "message": None if message is None else asdict(message)})
        return {**item, "source_messages": sources}

    @app.put("/api/host/scenes/{scene}/learning/expressions/{id}")
    async def update_expression(scene: str, id: int, change: ExpressionChange,
                                _: str = Depends(user)):
        chat_for(scene)
        try:
            item = learning.update_expression(scene, id, situation=change.situation,
                                              style=change.style, status=change.status)
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, f"{type(error).__name__}: {error}") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if item is None:
            raise HTTPException(404, "当前场景没有这条表达候选")
        runtime.notify()
        return item

    @app.delete("/api/host/scenes/{scene}/learning/expressions/{id}")
    async def delete_expression(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        if not learning.delete_expression(scene, id):
            raise HTTPException(404, "当前场景没有这条表达候选")
        runtime.notify()
        return {"deleted": True}
