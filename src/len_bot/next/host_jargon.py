"""Scene-local jargon candidates, actual model calls and explicit operator edits."""

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, field_validator, model_validator

from .config_types import STRICT
from .jargon_store import JargonStore
from .network import NetworkRuntime


class JargonChange(BaseModel):
    model_config = STRICT
    meaning: str | None
    status: Literal["pending", "adopted", "rejected"]

    @field_validator("meaning")
    @classmethod
    def nonblank_meaning(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("黑话解释不能只包含空白；清空请明确使用 null")
        return value

    @model_validator(mode="after")
    def adopted_meaning(self) -> "JargonChange":
        if self.status == "adopted" and self.meaning is None:
            raise ValueError("人工固定黑话词义时须填写有效解释")
        return self


def register_host_jargon(app: FastAPI, *, runtime: NetworkRuntime,
                         user: Callable[[Request], str]) -> None:
    records = JargonStore(runtime.store)
    base = "/api/host/scenes/{scene}/learning/jargon"

    def chat_for(scene: str):
        chat = runtime.chats.get(scene)
        if chat is None:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return chat

    def active(scene: str) -> bool:
        return runtime.jargon is not None and scene in runtime.jargon.scenes

    def service_state(scene: str) -> dict | None:
        return runtime.jargon.state(scene) if active(scene) else None

    @app.get(base)
    async def state(scene: str, _: str = Depends(user)):
        chat = chat_for(scene)
        return {"scene": scene, "enabled": active(scene),
                "selection_enabled": chat.config.learning is not None,
                "cursor": records.state(scene), "service_state": service_state(scene)}

    @app.get(base + "/terms")
    async def terms(scene: str, status: Literal["pending", "adopted", "rejected"] | None = None,
                    limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0),
                    _: str = Depends(user)):
        chat_for(scene)
        return records.items(scene, status=status, limit=limit, offset=offset)

    @app.get(base + "/terms/{id}")
    async def term(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        item = records.item(scene, id)
        if item is None:
            raise HTTPException(404, "当前场景没有这条黑话候选")
        sources = []
        for seq in item["sample_seqs"]:
            message = runtime.store.read_message(scene, seq)
            sources.append({"seq": seq, "body": None if message is None else asdict(message)})
        return {**item, "source_messages": sources}

    @app.put(base + "/terms/{id}")
    async def update(scene: str, id: int, change: JargonChange, _: str = Depends(user)):
        chat_for(scene)
        item = records.update(scene, id, meaning=change.meaning, status=change.status)
        if item is None:
            raise HTTPException(404, "当前场景没有这条黑话候选")
        runtime.notify()
        return item

    @app.delete(base + "/terms/{id}")
    async def delete(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        if not records.delete(scene, id):
            raise HTTPException(404, "当前场景没有这条黑话候选")
        runtime.notify()
        return {"deleted": True}

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
            raise HTTPException(404, "当前场景没有这次黑话模型调用")
        return item

    def trigger(scene: str, action: Literal["run", "retry"]) -> dict:
        chat_for(scene)
        if not active(scene):
            raise HTTPException(409, "当前运行场景未启用黑话后台学习")
        if not runtime.accepting:
            raise HTTPException(409, "宿主正在停止，不再接收学习请求")
        try:
            if action == "run":
                runtime.jargon.request(scene)
            else:
                runtime.jargon.retry(scene)
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        runtime.notify()
        return {"requested": action, "state": service_state(scene)}

    @app.post(base + "/run")
    async def run(scene: str, _: str = Depends(user)):
        return trigger(scene, "run")

    @app.post(base + "/retry")
    async def retry(scene: str, _: str = Depends(user)):
        return trigger(scene, "retry")
