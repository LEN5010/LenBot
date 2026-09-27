"""Single-scene web chat in an isolated root; never opens a platform outlet."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
import uvicorn

from len_bot.web.auth import (
    clear_login_failures, create_session, login_blocked, record_login_failure,
    revoke_session, session_user, verify_password,
)
from len_bot.web.shell import mount_panel
from .attention import SceneRunner
from .chat import Chat
from .config import LabConfig, STRICT, load_config
from .model import ChatModel
from .persona import load_persona
from .store import Store


COOKIE = "lenbot_test_session"


class Login(BaseModel):
    model_config = STRICT
    username: str
    password: str = Field(repr=False)


class TestMessage(BaseModel):
    model_config = STRICT
    uid: str = Field(pattern=r"^[1-9][0-9]*$")
    nickname: str = Field(min_length=1)
    text: str = Field(min_length=1)
    mention_bot: bool
    reply_to: str | None

    @field_validator("nickname", "text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PanelSession:
    def __init__(self, config: LabConfig, store: Store, mind: ChatModel, voice: ChatModel,
                 *, vision: ChatModel | None = None):
        self.config, self.store = config, store
        self.listeners: set[asyncio.Event] = set()
        self.closing = False
        self.chat = Chat(config, load_persona(config.persona), store, mind, voice, vision=vision, on_update=self.notify)
        self.runner = SceneRunner(self.chat, lambda _: self.notify(), resume=self.chat.restore())
        self.task = asyncio.create_task(self.runner.run())
        self.task.add_done_callback(lambda _: self.notify())

    def notify(self) -> None:
        for changed in self.listeners:
            changed.set()

    def error(self) -> str | None:
        if self.task.cancelled():
            return "CancelledError: isolated scene stopped"
        if self.task.done() and (error := self.task.exception()) is not None:
            return f"{type(error).__name__}: {error}"
        return None

    def snapshot(self) -> dict:
        return {
            "scene": self.config.scene, "timezone": self.config.timezone, "bot_qq": self.config.bot_qq,
            "persona": {"id": self.chat.persona.id, "name": self.chat.persona.name},
            "voice_mode": self.config.voice_mode,
            "models": {"mind": self.config.models.roles.mind.model, "voice": self.config.models.roles.voice.model},
            "delivery": "simulated", "running": not self.closing and not self.task.done(), "error": self.error(),
            "messages": [{"seq": seq, "rendered": self.chat.render(message), **asdict(message)}
                         for seq, message in self.store.recent_records(self.config.scene)],
            "turns": self.store.recent_turns(self.config.scene),
        }

    def receive(self, item: TestMessage) -> dict:
        if self.closing or self.task.done():
            raise HTTPException(503, self.error() or "隔离场景已停止，不接收新消息")
        kind, target = self.config.scene.split(":")
        if item.uid == self.config.bot_qq:
            raise HTTPException(422, "虚拟发言者不能使用 Bot 自己的 QQ")
        if kind == "private" and item.uid != target:
            raise HTTPException(422, "私聊测试身份必须是当前场景的对方 QQ")
        if item.reply_to is not None and self.store.find_message(self.config.scene, item.reply_to) is None:
            raise HTTPException(422, "引用的消息不在当前测试场景中")
        parts = []
        if item.reply_to is not None:
            parts.append({"type": "reply", "data": {"id": item.reply_to}})
        if item.mention_bot:
            parts.append({"type": "at", "data": {"qq": self.config.bot_qq}})
        parts.append({"type": "text", "data": {"text": item.text}})
        raw = {"post_type": "message", "message_type": kind, "self_id": self.config.bot_qq,
               "user_id": item.uid, "message_id": str(uuid4()), "time": time.time(),
               "sender": {"nickname": item.nickname, "card": "", "role": "member"}, "message": parts}
        if kind == "group":
            raw["group_id"] = target
        receipt = self.runner.receive(raw)
        self.notify()
        return receipt

    async def close(self) -> None:
        self.closing = True
        self.runner.close_input()
        await self.task


def create_app(config: LabConfig) -> FastAPI:
    if config.panel is None:
        raise ValueError("next.panel requires panel configuration in lenbot.config.json")
    if config.onebot is not None or config.delivery != "simulated":
        raise ValueError("next.panel requires onebot=null and delivery=simulated; no platform is opened")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with Store(config.database) as store:
            async with (
                ChatModel(config.model_settings("mind")) as mind,
                ChatModel(config.model_settings("voice")) as voice,
                (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
                 else nullcontext(None)) as vision,
            ):
                session = PanelSession(config, store, mind, voice, vision=vision)
                app.state.session = session
                try:
                    yield
                finally:
                    await session.close()

    app = FastAPI(title="LenBot 隔离对话测试", lifespan=lifespan)
    last_login_at: float | None = None

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path == "/api/auth/login":
            # A missing field otherwise echoes the complete password-bearing body.
            return JSONResponse(status_code=422, content={"detail": [
                {key: item[key] for key in ("type", "loc", "msg")} for item in error.errors()
            ]})
        return await request_validation_exception_handler(request, error)

    def user(request: Request) -> str:
        return session_user(request.cookies.get(COOKIE))

    @app.post("/api/auth/login")
    async def login(item: Login, request: Request, response: Response):
        nonlocal last_login_at
        key = f"isolated:{request.client.host}:{item.username}"
        if login_blocked(key):
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
        valid = (item.username == config.panel.username and
                 await asyncio.to_thread(verify_password, item.password, config.panel.password_hash))
        if not valid:
            record_login_failure(key)
            raise HTTPException(401, "Invalid username or password")
        clear_login_failures(key)
        last_login_at = time.time()
        response.set_cookie(COOKIE, create_session(item.username), httponly=True, samesite="lax",
                            secure=config.panel.cookie_secure, max_age=7 * 86400)
        return {"success": True, "username": item.username, "is_default_password": False}

    @app.get("/api/auth/me")
    async def me(current: str = Depends(user)):
        return {"username": current, "is_default_password": False, "last_login_at": last_login_at}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response, _: str = Depends(user)):
        revoke_session(request.cookies[COOKIE])
        response.delete_cookie(COOKIE)
        app.state.session.notify()
        return {"success": True}

    @app.get("/api/chat-test/state")
    async def state(_: str = Depends(user)):
        return app.state.session.snapshot()

    @app.post("/api/chat-test/messages")
    async def message(item: TestMessage, _: str = Depends(user)):
        return app.state.session.receive(item)

    @app.get("/api/chat-test/turns/{turn_id}")
    async def turn(turn_id: str, _: str = Depends(user)):
        result = app.state.session.store.turn_detail(config.scene, turn_id)
        if result is None:
            raise HTTPException(404, "当前测试场景没有这一轮")
        return result

    @app.websocket("/api/chat-test/events")
    async def events(websocket: WebSocket):
        token = websocket.cookies.get(COOKIE)
        try:
            session_user(token)
        except HTTPException:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session: PanelSession = app.state.session
        changed = asyncio.Event()
        session.listeners.add(changed)
        changed.set()

        async def push():
            while True:
                await changed.wait()
                changed.clear()
                session_user(token)
                await websocket.send_json({"type": "changed"})

        pushing = asyncio.create_task(push())
        receiving = asyncio.create_task(websocket.receive())
        try:
            done, _ = await asyncio.wait({pushing, receiving}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = await task
                if task is receiving and result["type"] != "websocket.disconnect":
                    await websocket.close(code=1003, reason="This stream only reports changes")
        except HTTPException:
            await websocket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            session.listeners.remove(changed)
            pushing.cancel()
            receiving.cancel()
            await asyncio.gather(pushing, receiving, return_exceptions=True)

    mount_panel(app, mode="isolated", home="/chat-test", assets_dir=config.panel.assets_dir)
    return app


def main() -> None:
    config = load_config(Path.cwd())
    app = create_app(config)
    uvicorn.run(app, host=config.panel.host, port=config.panel.port)


if __name__ == "__main__":
    main()
