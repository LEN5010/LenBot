"""Authenticated scene-local expression learning records and operator decisions."""

from dataclasses import asdict
import logging
import sqlite3
from typing import Callable, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ...configuration.types import STRICT
from ...learning.store import LearningStore
from ...runtime.network import NetworkRuntime

logger = logging.getLogger(__name__)


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
                and chat_for(scene).config.learning is not None
                and chat_for(scene).config.learning.extract)

    def selection_enabled(scene: str) -> bool:
        return runtime.expression_service is not None and scene in runtime.expression_service.scenes

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
                "selection_enabled": selection_enabled(scene), "voice_mode": chat.config.voice_mode,
                "settings": None if chat.config.learning is None else
                chat.config.learning.model_dump(mode="json"),
                "cursor": learning.state(scene),
                "latest": None if latest is None else {
                    key: latest[key] for key in ("id", "after_seq", "through_seq", "started", "ended",
                                                  "status", "model_started", "usage", "tokens", "error")},
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

    @app.get("/api/host/scenes/{scene}/learning/embedding-calls")
    async def embedding_calls(scene: str, limit: int = Query(20, ge=1, le=20),
                              offset: int = Query(0, ge=0), _: str = Depends(user)):
        chat_for(scene)
        return learning.embedding_calls(scene, limit=limit, offset=offset)

    @app.get("/api/host/scenes/{scene}/learning/embedding-calls/{id}")
    async def embedding_call(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        item = learning.embedding_call(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这一表达向量请求")
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
                            "rendered": None if message is None else chat.context.render(message),
                            "message": None if message is None else asdict(message)})
        return {**item, "source_messages": sources}

    @app.put("/api/host/scenes/{scene}/learning/expressions/{id}")
    async def update_expression(scene: str, id: int, change: ExpressionChange,
                                _: str = Depends(user)):
        chat_for(scene)
        service = runtime.expression_service if selection_enabled(scene) else None
        try:
            item = (await service.update(scene, id, situation=change.situation,
                                         style=change.style, status=change.status)
                    if service is not None else
                    learning.update_expression(scene, id, situation=change.situation,
                                               style=change.style, status=change.status))
        except sqlite3.IntegrityError as error:
            logger.exception("更新表达候选冲突：scene=%s id=%s", scene, id)
            raise HTTPException(409, f"{type(error).__name__}: {error}") from error
        except ValueError as error:
            logger.exception("更新表达候选被拒绝：scene=%s id=%s", scene, id)
            raise HTTPException(409 if service is not None else 422, str(error)) from error
        except (httpx.HTTPError, TimeoutError) as error:
            logger.exception("更新表达候选向量请求失败：scene=%s id=%s", scene, id)
            raise HTTPException(502, f"{type(error).__name__}: {error}") from error
        except Exception as error:
            logger.exception("更新表达候选失败：scene=%s id=%s", scene, id)
            raise HTTPException(500, f"{type(error).__name__}: {error}") from error
        if item is None:
            raise HTTPException(404, "当前场景没有这条表达候选")
        runtime.notify()
        return item

    @app.delete("/api/host/scenes/{scene}/learning/expressions/{id}")
    async def delete_expression(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        service = runtime.expression_service if selection_enabled(scene) else None
        try:
            deleted = (await service.delete(scene, id) if service is not None else
                       learning.delete_expression(scene, id))
        except ValueError as error:
            logger.exception("删除表达候选被拒绝：scene=%s id=%s", scene, id)
            raise HTTPException(409 if service is not None else 422, str(error)) from error
        except (httpx.HTTPError, TimeoutError) as error:
            logger.exception("删除表达候选向量请求失败：scene=%s id=%s", scene, id)
            raise HTTPException(502, f"{type(error).__name__}: {error}") from error
        except Exception as error:
            logger.exception("删除表达候选失败：scene=%s id=%s", scene, id)
            raise HTTPException(500, f"{type(error).__name__}: {error}") from error
        if not deleted:
            raise HTTPException(404, "当前场景没有这条表达候选")
        runtime.notify()
        return {"deleted": True}
