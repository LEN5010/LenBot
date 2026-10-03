"""Authenticated reply effect samples, frozen follow-ups and explicit judge requests."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from .network import NetworkRuntime
from .reply_effect_store import CHANNELS, REACTIONS, STATES, ReplyEffectStore


# The same names the store derives; "attention" selects corrections and negative reactions.
StateFilter = Literal[(*REACTIONS, *STATES, "attention")]
ChannelFilter = Literal[CHANNELS]


def register_host_reply_effects(app: FastAPI, *, runtime: NetworkRuntime,
                                user: Callable[[Request], str]) -> None:
    records = ReplyEffectStore(runtime.store)

    def chat_for(scene: str):
        chat = runtime.chats.get(scene)
        if chat is None:
            raise HTTPException(404, "当前宿主未配置这一场景")
        return chat

    def active(scene: str) -> bool:
        chat = chat_for(scene)
        return (runtime.reply_effects is not None and scene in runtime.reply_effects.scenes
                and chat.config.learning is not None and chat.config.learning.reply_effects)

    def rendered(scene: str, seqs: list[int], arrivals: dict[int, float | None] | None = None) -> list[dict]:
        chat = chat_for(scene)
        result = []
        for seq in seqs:
            message = runtime.store.read_message(scene, seq)
            result.append({"record": seq, "available": message is not None,
                           "received_at": None if arrivals is None else arrivals.get(seq),
                           "rendered": None if message is None else chat.context.render(message),
                           "message": None if message is None else asdict(message)})
        return result

    @app.get("/api/host/scenes/{scene}/reply-effects")
    async def state(scene: str, days: int = Query(7, ge=1, le=90), _: str = Depends(user)):
        chat = chat_for(scene)
        since = runtime.store.now() - days * 86400
        return {"scene": scene, "enabled": active(scene), "days": days,
                "delivery": "simulated" if chat.expression.send_message is None else "onebot",
                "settings": None if chat.config.learning is None else {
                    "reply_effects": chat.config.learning.reply_effects,
                    "max_age_seconds": chat.config.learning.max_age_seconds},
                "distribution": records.distribution(scene, since),
                "service_state": runtime.reply_effects.state(scene) if active(scene) else None}

    @app.get("/api/host/scenes/{scene}/reply-effects/items")
    async def items(scene: str, state: StateFilter | None = None, channel: ChannelFilter | None = None,
                    days: int | None = Query(None, ge=1, le=90),
                    limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0),
                    _: str = Depends(user)):
        chat_for(scene)
        since = None if days is None else runtime.store.now() - days * 86400
        return records.page(scene, state=state, channel=channel, since=since, limit=limit, offset=offset)

    @app.get("/api/host/scenes/{scene}/reply-effects/items/{id}")
    async def item(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        effect = records.effect(scene, id)
        if effect is None:
            raise HTTPException(404, "当前场景没有这条回复效果样本")
        observed = effect["observed_seqs"] or []
        return {**effect,
                "expression": rendered(scene, effect["message_seqs"]),
                "before": [{"record": seq, "rendered": chat_for(scene).context.render(message)}
                           for seq, message in records.before(scene, min(effect["message_seqs"]))],
                "followups": rendered(scene, observed, records.arrivals(scene, observed)),
                "call": None if effect["call_id"] is None else records.call(scene, effect["call_id"], summary=True)}

    @app.get("/api/host/scenes/{scene}/reply-effects/calls")
    async def calls(scene: str, limit: int = Query(20, ge=1, le=20), offset: int = Query(0, ge=0),
                    _: str = Depends(user)):
        chat_for(scene)
        return records.calls(scene, limit=limit, offset=offset)

    @app.get("/api/host/scenes/{scene}/reply-effects/calls/{id}")
    async def call(scene: str, id: int, _: str = Depends(user)):
        chat_for(scene)
        result = records.call(scene, id)
        if result is None:
            raise HTTPException(404, "当前场景没有这一回复效果判断")
        return result

    @app.get("/api/host/scenes/{scene}/turns/{turn_id}/reply-effects")
    async def turn_effects(scene: str, turn_id: str, _: str = Depends(user)):
        chat_for(scene)
        return {"items": records.for_turn(scene, turn_id)}

    def trigger(scene: str, action: Callable[[], None]) -> dict:
        chat_for(scene)
        if not active(scene):
            raise HTTPException(409, "当前运行场景未启用回复效果")
        if not runtime.accepting:
            raise HTTPException(409, "宿主正在停止，不再接收判断请求")
        try:
            action()
        except (ValueError, RuntimeError) as error:
            raise HTTPException(409, str(error)) from error
        runtime.notify()
        return {"requested": True, "state": runtime.reply_effects.state(scene)}

    @app.post("/api/host/scenes/{scene}/reply-effects/request")
    async def request(scene: str, _: str = Depends(user)):
        return trigger(scene, lambda: runtime.reply_effects.request(scene))

    @app.post("/api/host/scenes/{scene}/reply-effects/calls/{id}/retry")
    async def retry(scene: str, id: int, _: str = Depends(user)):
        return trigger(scene, lambda: runtime.reply_effects.retry(scene, id))
