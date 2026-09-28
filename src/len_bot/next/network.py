"""One OneBot connection for explicitly configured isolated chat scenes."""

from __future__ import annotations

import asyncio
import signal
import sqlite3
from collections.abc import Callable

from .attention import SceneRunner
from .chat import Chat
from .config import LabConfig, OneBotForward, SharedConfig
from .messages import parse_message
from .model import ChatModel
from .model_slots import ModelSlots
from .memory import MemoryService
from .memory_ingest import MemoryIngestor
from .learning import ExpressionLearner
from .jargon import JargonLearner
from .sticker_collection import StickerCollector
from .reply_effects import ReplyEffectTracker
from .expression_selection import ExpressionService
from .onebot import OneBot
from .persona import Persona
from .plugin_host import PluginHost
from .store import Store, encode
from .tasks import WorkTasks


class NetworkRuntime:
    def __init__(self, config: SharedConfig, scene_configs: list[tuple[LabConfig, Persona]],
                 store: Store, mind: ChatModel, voice: ChatModel, *,
                 vision: ChatModel | None = None, slots: ModelSlots | None = None,
                 memory: MemoryService | None = None,
                 ingestor: MemoryIngestor | None = None,
                 tasks: WorkTasks | None = None,
                 learning: ExpressionLearner | None = None,
                 jargon: JargonLearner | None = None,
                 sticker_collection: StickerCollector | None = None,
                 reply_effects: ReplyEffectTracker | None = None,
                 expression_service: ExpressionService | None = None,
                 plugins: PluginHost | None = None,
                 on_update: Callable[[], None] | None = None):
        if config.onebot is None:
            raise ValueError("Network input requires OneBot configuration")
        self.config, self.store = config, store
        self.memory = memory
        self.ingestor = ingestor
        self.learning = learning
        self.jargon = jargon
        self.sticker_collection = sticker_collection
        if sticker_collection is not None:
            sticker_collection.on_update = self.notify
        self.reply_effects = reply_effects
        # Host time when the current OneBot connection began; None while disconnected.
        self.connected_since: float | None = None
        if reply_effects is not None:
            reply_effects.on_update = self.notify
            reply_effects.connected_since = lambda: self.connected_since
        if jargon is not None:
            jargon.on_update = self.notify
        self.expression_service = expression_service
        if expression_service is not None:
            expression_service.on_update = self.notify
        if learning is not None:
            learning.on_update = self.notify
        self.tasks = tasks
        self.plugins = plugins
        if plugins is not None:
            plugins.on_update = self.notify
        self.on_update = on_update
        self.status = "created"
        self.last_platform_error: str | None = None
        self.stopped = asyncio.Event()
        self.accepting = True
        self.storage_error: sqlite3.Error | None = None
        self.platform = OneBot(config.onebot, bot_qq=config.bot_qq, on_event=self._receive,
                               on_error=self._platform_error, on_connection_change=self._connection_changed)
        self.chats: dict[str, Chat] = {}
        for scene_config, persona in scene_configs:
            scene = scene_config.scene
            if scene in self.chats:
                raise ValueError(f"Duplicate network scene {scene}")
            self.chats[scene] = Chat(
                scene_config, persona, store, mind, voice, vision=vision, slots=slots, memory=memory,
                tasks=tasks, expression_service=expression_service,
                on_compaction=lambda scene=scene: self.compacted(scene),
                on_reply_sample=(None if reply_effects is None else
                                 lambda scene=scene: reply_effects.wake(scene)),
                send_message=self.platform.send_message if config.delivery == "onebot" else None,
                upload_file=self.platform.upload_file if config.delivery == "onebot" else None,
                platform_call=self.platform.call if config.delivery == "onebot" else None,
                external_tools=[] if plugins is None else plugins.tools_for(scene),
                on_update=self.notify,
            )
        self.runners: dict[str, SceneRunner] = {}
        for scene, chat in self.chats.items():
            self.runners[scene] = SceneRunner(
                chat, lambda result, scene=scene: self._emit({"type": "turn", **result, "scene": scene}),
                resume=chat.restore(), ready_for_turn=self._ready,
                connected_since=lambda: self.connected_since,
            )
        if plugins is not None:
            plugins.bind(self)

    def compacted(self, scene: str) -> None:
        if self.ingestor is not None:
            self.ingestor.request(scene)
        if self.learning is not None and scene in self.learning.scenes:
            state = self.learning.state(scene)
            latest = state["latest"]
            if (state["running"] and state["worker_error"] is None
                    and (latest is None or latest["status"] == "complete")):
                self.learning.request(scene)
        if self.jargon is not None and scene in self.jargon.scenes:
            state = self.jargon.state(scene)
            latest = state["latest"]
            if (state["running"] and state["worker_error"] is None
                    and (latest is None or latest["status"] == "complete")):
                self.jargon.request(scene)

    def notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

    def _connection_changed(self) -> None:
        self.connected_since = self.store.now() if self.platform.connected else None
        self.notify()

    def _status(self, status: str) -> None:
        if self.status != status:
            self.status = status
            self.notify()

    def _emit(self, result: dict) -> None:
        print(encode(result), flush=True)
        self.notify()

    def _platform_error(self, error: str) -> None:
        self.last_platform_error = error
        self._emit({"type": "platform_error", "error": error})

    def stop(self) -> None:
        self.accepting = False
        if self.tasks is not None:
            self.tasks.stop()
        self.stopped.set()
        if self.status != "stopped":
            self._status("stopping")

    def _receive(self, raw: dict) -> None:
        post_type = raw["post_type"]
        if post_type == "meta_event":
            return
        if post_type != "message":
            if not self.accepting:
                self._emit({"type": "receipt", "status": "not_accepted", "reason": "stopping"})
            elif post_type == "notice" and self.plugins is not None:
                try:
                    handled = self.plugins.handle_notice(raw)
                except ValueError as error:
                    self._platform_error(f"{type(error).__name__}: {error}")
                    return
                self._emit({"type": "platform_event", "post_type": post_type,
                            "notice_type": raw["notice_type"], "plugin_handlers": handled})
            else:
                self._emit({"type": "platform_event", "status": "unsupported", "post_type": post_type})
            return
        message = parse_message(raw, own_message_ids=set())
        if not self.accepting:
            self._emit({"type": "receipt", "scene": message.scene,
                        "status": "not_accepted", "reason": "stopping"})
            return
        runner = self.runners.get(message.scene)
        if runner is None:
            self._emit({"type": "receipt", "scene": message.scene, "status": "ignored",
                        "platform_message_id": message.platform_message_id})
            return
        command = (None if self.plugins is None else
                   self.plugins.match_command(message, tuple(runner.settings.other_bot_qqs)))
        try:
            receipt = runner.receive_message(message, raw, wake=command is None)
        except sqlite3.Error as error:
            self.storage_error = error
            self.stop()
            raise
        if (self.reply_effects is not None and receipt["status"] != "duplicate" and not message.is_self):
            self.reply_effects.wake(message.scene)
        if (self.sticker_collection is not None and message.scene in self.sticker_collection.scenes
                and message.scene not in self.sticker_collection.errors
                and receipt["status"] != "duplicate" and not message.is_self
                and any(segment.type == "image" for segment in message.segments)):
            self.sticker_collection.request(message.scene)
        if command is not None and receipt["status"] != "duplicate":
            self.plugins.dispatch_command(message, command)
            receipt["plugin_command"] = f"{command[0].name} /{command[1]}"
        self._emit({"type": "receipt", **receipt, "scene": message.scene})

    async def _ready(self, wait: bool) -> bool:
        if self.platform.connected:
            return True
        if not wait:
            return False
        if self.stopped.is_set() or isinstance(self.config.onebot, OneBotForward):
            return False
        async with asyncio.TaskGroup() as waiting:
            connected = waiting.create_task(self.platform.wait_connected(None))
            stopping = waiting.create_task(self.stopped.wait())
            try:
                await asyncio.wait({connected, stopping}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                connected.cancel()
                stopping.cancel()
        return self.platform.connected

    async def run(self, *, manage_signals: bool = True) -> None:
        if self.stopped.is_set():
            self._status("stopped")
            return
        self._status("starting")
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        try:
            if manage_signals:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, self.stop)
                    installed.append(sig)
            if self.tasks is not None:
                await self.tasks.recover()
            async with asyncio.TaskGroup() as group:
                pending: list[asyncio.Task] = []
                try:
                    stopping = group.create_task(self.stopped.wait())
                    starting = group.create_task(self.platform.start())
                    pending.extend([stopping, starting])
                    done, _ = await asyncio.wait({starting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    if stopping in done:
                        return
                    try:
                        await starting
                    except Exception as error:
                        self.last_platform_error = f"{type(error).__name__}: {error}"
                        self.notify()
                        raise
                    self._status("waiting_connection")
                    self._emit({"type": "runtime", "status": "started", "input": "onebot",
                                "delivery": self.config.delivery, "addresses": self.platform.addresses})
                    terminated = group.create_task(self.platform.wait_terminated())
                    connected = group.create_task(self.platform.wait_connected(None))
                    pending.extend([terminated, connected])
                    done, _ = await asyncio.wait({connected, stopping, terminated}, return_when=asyncio.FIRST_COMPLETED)
                    if connected not in done or stopping in done or terminated in done:
                        return
                    await connected
                    if self.tasks is not None:
                        await self.tasks.start()
                        pending.append(group.create_task(self.tasks.wait_failure()))
                    if self.learning is not None:
                        self.learning.start()
                    if self.jargon is not None:
                        self.jargon.start()
                    if self.sticker_collection is not None:
                        self.sticker_collection.start()
                    if self.reply_effects is not None:
                        self.reply_effects.start()
                    if self.plugins is not None:
                        await self.plugins.start()
                    self._status("running")
                    self._emit({"type": "runtime", "status": "ready", "input": "onebot",
                                "delivery": self.config.delivery})
                    running = [group.create_task(runner.run()) for runner in self.runners.values()]
                    pending.extend(running)
                    done, _ = await asyncio.wait({*running, stopping, terminated}, return_when=asyncio.FIRST_COMPLETED)
                    self.accepting = False
                    reason = ("storage_error" if self.storage_error is not None else
                              "signal" if stopping in done else "transport_closed" if terminated in done
                              else "scene_stopped")
                    self._status("stopping")
                    self._emit({"type": "runtime", "status": "stopping", "reason": reason})
                    self.stopped.set()
                    if self.plugins is not None:
                        await self.plugins.close()
                    if self.reply_effects is not None:
                        await self.reply_effects.close()
                    if self.sticker_collection is not None:
                        await self.sticker_collection.close()
                    if self.jargon is not None:
                        await self.jargon.close()
                    if self.learning is not None:
                        await self.learning.close()
                    if self.tasks is not None:
                        await self.tasks.close()
                    for runner in self.runners.values():
                        runner.close_input()
                    await asyncio.gather(*running)
                finally:
                    self.accepting = False
                    for task in pending:
                        task.cancel()
        finally:
            try:
                try:
                    if self.plugins is not None:
                        await self.plugins.close()
                    if self.tasks is not None:
                        await self.tasks.close()
                finally:
                    try:
                        if self.learning is not None:
                            await self.learning.close()
                    finally:
                        try:
                            if self.jargon is not None:
                                await self.jargon.close()
                        finally:
                            try:
                                if self.sticker_collection is not None:
                                    await self.sticker_collection.close()
                            finally:
                                try:
                                    if self.reply_effects is not None:
                                        await self.reply_effects.close()
                                finally:
                                    await self.platform.close()
                    self._status("stopped")
                    self._emit({"type": "runtime", "status": "stopped"})
            finally:
                for sig in installed:
                    loop.remove_signal_handler(sig)
            if self.storage_error is not None:
                raise self.storage_error


async def run_network(config: SharedConfig, scene_configs: list[tuple[LabConfig, Persona]],
                      store: Store, mind: ChatModel, voice: ChatModel, *,
                      vision: ChatModel | None = None, slots: ModelSlots | None = None,
                      memory: MemoryService | None = None, ingestor: MemoryIngestor | None = None,
                      expression_service: ExpressionService | None = None) -> None:
    runtime = NetworkRuntime(config, scene_configs, store, mind, voice, vision=vision, slots=slots,
                             memory=memory, ingestor=ingestor, expression_service=expression_service)
    await runtime.run()
