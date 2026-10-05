"""Single-scene web chat in an isolated root; never opens a platform outlet."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from threading import Lock
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, WebSocket
from pydantic import BaseModel, Field, field_validator
import uvicorn

from len_bot.web.shell import mount_panel
from ..chat.attention import SceneRunner
from ..chat.session import Chat
from ..platform.messages import ChatMessage, Sender, Segment
from ..config import LabConfig, load_config, read_scene_persona, save_scene_persona
from ..configuration.chat import ScenePersona
from ..configuration.types import STRICT
from ..instance_lock import instance_lock
from ..models.client import ChatModel
from ..memory.service import MemoryService, open_memory
from ..memory.ingest import MemoryIngestor, open_memory_ingestor
from ..panel.auth import changes_socket, install_panel_auth
from ..persona.profile import Persona, load_persona, select_examples
from ..models.slots import ModelSlots
from ..models.limits import ModelBudget
from ..storage.store import Store


logger = logging.getLogger(__name__)


class TestMessage(BaseModel):
    model_config = STRICT
    uid: str = Field(pattern=r"^[a-z][a-z0-9_-]*:[^:\s/\\]+$")
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
    def __init__(self, config: LabConfig, store: Store, mind: ChatModel,
                 *, vision: ChatModel | None = None, memory: MemoryService | None = None,
                 ingestor: MemoryIngestor | None = None, persona: Persona | None = None,
                 slots: ModelSlots | None = None):
        self.config, self.store = config, store
        if slots is None:
            slots = ModelSlots(config.max_model_requests)
            slots.admit = ModelBudget(config, store, memory, root=config._instance_root).check
        self.listeners: set[asyncio.Event] = set()
        self.closing = False
        self.chat = Chat(config, load_persona(config.persona) if persona is None else persona, store, mind, vision=vision,
                         memory=memory, slots=slots, on_update=self.notify,
                         on_compaction=None if ingestor is None else lambda: ingestor.request(config.scene))
        self.runner = SceneRunner(self.chat, lambda _: self.notify())
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
            "scene": self.config.scene, "timezone": self.config.timezone, "bot_id": self.config.bot_id,
            "persona": {"id": self.chat.persona.id, "name": self.chat.persona.name},
            "voice_mode": self.config.voice_mode,
            "models": {"mind": self.config.models.roles.mind.model},
            "delivery": "simulated", "running": not self.closing and not self.task.done(), "error": self.error(),
            "messages": [{"seq": seq, "rendered": self.chat.context.render(message), "text": self.chat.context.render_text(message),
                          **asdict(message)}
                         for seq, message in self.store.recent_records(self.config.scene)],
            "turns": self.store.recent_turns(self.config.scene),
        }

    def receive(self, item: TestMessage) -> dict:
        if self.closing or self.task.done():
            raise HTTPException(503, self.error() or "隔离场景已停止，不接收新消息")
        platform, kind, target = self.config.scene.split(":", 2)
        if item.uid == self.config.bot_id:
            raise HTTPException(422, "虚拟发言者不能使用 Bot 自己的账号")
        if kind == "private" and item.uid != platform + ":" + target:
            raise HTTPException(422, "私聊测试身份必须是当前场景的对方账号")
        if item.reply_to is not None and self.store.find_message(self.config.scene, item.reply_to) is None:
            raise HTTPException(422, "引用的消息不在当前测试场景中")
        parts = []
        if item.reply_to is not None:
            parts.append(Segment("reply", {"id": item.reply_to}))
        if item.mention_bot:
            parts.append(Segment("mention", {"user": self.config.bot_id}))
        parts.append(Segment("text", {"text": item.text}))
        message = ChatMessage(id=str(uuid4()), platform=platform, bot_id=self.config.bot_id,
                              scene=self.config.scene, platform_message_id=str(uuid4()),
                              sender=Sender(item.uid, item.nickname, None, "member"), time=time.time(),
                              segments=parts, reply_to=item.reply_to, mentions_bot=item.mention_bot,
                              is_self=False, send_status="received")
        receipt = self.runner.receive_message(message, asdict(message))
        self.notify()
        return receipt

    async def close(self) -> None:
        self.closing = True
        self.runner.close_input()
        await self.task


def create_app(config: LabConfig, *, root: Path) -> FastAPI:
    if config.panel is None:
        raise ValueError("next.panel requires panel configuration in lenbot.config.json")
    if config.onebot is not None or config.delivery != "simulated":
        raise ValueError("next.panel requires onebot=null and delivery=simulated; no platform is opened")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        persona = load_persona(config.persona)
        with Store(config.database) as store:
            slots = ModelSlots(config.max_model_requests)
            budget = ModelBudget(config, store, None, root=config._instance_root)
            slots.admit = budget.check
            async with (
                ChatModel(config.model_settings("mind")) as mind,
                (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
                 else nullcontext(None)) as vision,
                open_memory(config, store, active_personas={config.scene: persona.id}, slots=slots) as memory,
                open_memory_ingestor(config, store, memory, [config.scene], slots=slots) as ingestor,
            ):
                budget.memory = memory
                session = PanelSession(config, store, mind, vision=vision, memory=memory, ingestor=ingestor, slots=slots,
                                       persona=persona)
                app.state.session = session
                try:
                    yield
                finally:
                    await session.close()

    app = FastAPI(title="LenBot 隔离对话测试", lifespan=lifespan)
    config_write_lock = Lock()
    user = install_panel_auth(app, config.panel, on_logout=lambda: app.state.session.notify())

    @app.get("/api/chat-test/state")
    async def state(_: str = Depends(user)):
        return app.state.session.snapshot()

    @app.get("/api/chat-test/settings")
    async def settings(_: str = Depends(user)):
        session: PanelSession = app.state.session
        config, chat = session.config, session.chat
        persona = chat.persona
        return {
            "scene": config.scene, "timezone": config.timezone, "bot_id": config.bot_id,
            "voice_mode": config.voice_mode, "delivery": "simulated",
            "scene_persona": {
                "persona_aliases": config.persona_aliases,
                "relationships": config.relationships,
                "behavior_addendum": config.behavior_addendum,
            },
            "persona": persona.model_dump(),
            "selected_examples": [example.model_dump() for example in select_examples(persona)],
            "knowledge": [
                {"filename": filename, "tags": list(document.tags), "characters": len(document.content)}
                for filename, document in sorted(persona.knowledge.items())
            ],
            "tools": {
                "core": sorted(tool["function"]["name"] for tool in chat.toolset.core_tools),
                "deferred": sorted(tool["function"]["name"] for tool in chat.toolset.deferred_tools),
            },
        }

    def scene_persona_result(saved: ScenePersona) -> dict:
        values = saved.model_dump()
        loaded = {field: getattr(config, field) for field in ScenePersona.model_fields}
        return {"saved": values, "restart_required": values != loaded}

    @app.get("/api/chat-test/scene-persona")
    def scene_persona(_: str = Depends(user)):
        try:
            with config_write_lock:
                saved = read_scene_persona(root)
        except (ValueError, OSError) as error:
            logger.exception("读取本场景补充失败：%s", error)
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
        return scene_persona_result(saved)

    @app.put("/api/chat-test/scene-persona")
    def put_scene_persona(item: ScenePersona, _: str = Depends(user)):
        try:
            with config_write_lock:
                save_scene_persona(root, item)
        except (ValueError, OSError) as error:
            logger.exception("保存本场景补充失败：%s", error)
            raise HTTPException(422 if isinstance(error, ValueError) else 500,
                                f"{type(error).__name__}: {error}") from error
        return scene_persona_result(item)

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
        await changes_socket(websocket, app.state.session.listeners)

    mount_panel(app, mode="isolated", home="/chat-test", assets_dir=config.panel.assets_dir)
    return app


def main() -> None:
    root = Path.cwd().resolve()
    with instance_lock(root):
        config = load_config(root)
        app = create_app(config, root=root)
        uvicorn.run(app, host=config.panel.host, port=config.panel.port)


if __name__ == "__main__":
    main()
