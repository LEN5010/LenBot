"""Authenticated collected-sticker originals, operator decisions and actual attempts."""

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, field_validator, model_validator

from .config import STRICT
from .network import NetworkRuntime
from .sticker_store import StickerStore


class StickerChange(BaseModel):
    model_config = STRICT
    description: str | None
    text: str | None
    emotions: list[str]
    tags: list[str]
    review: Literal["pending", "adopted", "rejected"]

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("表情描述不能只包含空白；未标注时使用 null")
        return value

    @field_validator("emotions", "tags")
    @classmethod
    def nonblank_labels(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("表情标签不能包含空白项")
        return values

    @model_validator(mode="after")
    def adoption_description(self) -> "StickerChange":
        if self.review == "adopted" and self.description is None:
            raise ValueError("采用表情前须填写描述")
        return self


def register_host_stickers(app: FastAPI, *, runtime: NetworkRuntime,
                           user: Callable[[Request], str]) -> None:
    records = StickerStore(runtime.store)
    base = "/api/host/scenes/{scene}/learning/stickers"

    def chat_for(scene: str):
        chat = runtime.chats.get(scene)
        if chat is None:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return chat

    def active(scene: str) -> bool:
        return runtime.sticker_collection is not None and scene in runtime.sticker_collection.scenes

    def service_state(scene: str) -> dict | None:
        return runtime.sticker_collection.state(scene) if active(scene) else None

    def service_for(scene: str):
        chat_for(scene)
        if not active(scene):
            raise HTTPException(409, "当前运行场景未启用群表情收集")
        if not runtime.accepting:
            raise HTTPException(409, "宿主正在停止，不再接收收集请求")
        return runtime.sticker_collection

    @app.get(base)
    async def state(scene: str, _: str = Depends(user)):
        chat_for(scene)
        return {"scene": scene, "enabled": active(scene), "service_state": service_state(scene),
                "counts": records.counts(scene)}

    @app.get(base + "/candidates")
    async def candidates(scene: str, review: Literal["pending", "adopted", "rejected"] | None = None,
                         status: Literal["queued", "running", "complete", "failed", "interrupted"] | None = None,
                         limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0),
                         _: str = Depends(user)):
        chat_for(scene)
        return records.items(scene, review=review, status=status, limit=limit, offset=offset)

    @app.get(base + "/candidates/{id}")
    async def candidate(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        item = records.item(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这张表情候选")
        message = runtime.store.read_message(scene, item["source_message_seq"])
        return {**item, "source_message": {"seq": item["source_message_seq"],
                                          "body": None if message is None else asdict(message)}}

    @app.get(base + "/candidates/{id}/image")
    async def original(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        asset = records.original(scene, id)
        if asset is None:
            raise HTTPException(404, "当前场景没有该候选的已保存原件")
        return Response(asset[1], media_type=asset[0], headers={"Cache-Control": "private, no-store"})

    @app.put(base + "/candidates/{id}")
    async def update(scene: str, id: int, change: StickerChange, _: str = Depends(user)):
        chat_for(scene)
        try:
            item = records.update(scene, id, **change.model_dump())
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if item is None:
            raise HTTPException(404, "当前场景没有这张表情候选")
        runtime.notify()
        return item

    @app.delete(base + "/candidates/{id}")
    async def delete(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        try:
            deleted = records.delete(scene, id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if not deleted:
            raise HTTPException(404, "当前场景没有这张表情候选")
        runtime.notify()
        return {"deleted": True}

    @app.post(base + "/candidates/{id}/retry")
    async def retry(scene: str, id: int, _: str = Depends(user)):
        service = service_for(scene)
        if records.item(scene, id) is None:
            raise HTTPException(404, "当前场景没有这张表情候选")
        try:
            service.retry(scene, id)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        runtime.notify()
        return {"requested": True, "candidate": records.item(scene, id), "state": service_state(scene)}

    @app.post(base + "/run")
    async def run(scene: str, _: str = Depends(user)):
        service = service_for(scene)
        try:
            service.request(scene)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        return {"requested": True, "state": service_state(scene)}

    @app.get(base + "/calls")
    async def calls(scene: str, limit: int = Query(20, ge=1, le=20),
                    offset: int = Query(0, ge=0), _: str = Depends(user)):
        chat_for(scene)
        return records.calls(scene, limit=limit, offset=offset)

    @app.get(base + "/calls/{id}")
    async def call(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        item = records.call(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这次表情处理记录")
        message = runtime.store.read_message(scene, item["source_message_seq"])
        return {**item, "source_message": {"seq": item["source_message_seq"],
                                          "body": None if message is None else asdict(message)}}
