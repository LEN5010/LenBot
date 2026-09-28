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
from .expression_selection import ExpressionService
from .onebot import OneBot
from .persona import Persona
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
                 expression_service: ExpressionService | None = None,
                 on_update: Callable[[], None] | None = None):
        if config.onebot is None:
            raise ValueError("Network input requires OneBot configuration")
        self.config, self.store = config, store
        self.memory = memory
        self.ingestor = ingestor
        self.learning = learning
        self.expression_service = expression_service
        if expression_service is not None:
            expression_service.on_update = self.notify
        if learning is not None:
            learning.on_update = self.notify
        self.tasks = tasks
        self.on_update = on_update
        self.status = "created"
        self.last_platform_error: str | None = None
        self.stopped = asyncio.Event()
        self.accepting = True
        self.storage_error: sqlite3.Error | None = None
        self.platform = OneBot(config.onebot, bot_qq=config.bot_qq, on_event=self._receive,
                               on_error=self._platform_error, on_connection_change=self.notify)
        self.chats: dict[str, Chat] = {}
        for scene_config, persona in scene_configs:
            scene = scene_config.scene
            if scene in self.chats:
                raise ValueError(f"Duplicate network scene {scene}")
            self.chats[scene] = Chat(
                scene_config, persona, store, mind, voice, vision=vision, slots=slots, memory=memory,
                tasks=tasks, expression_service=expression_service,
                on_compaction=lambda scene=scene: self.compacted(scene),
                send_message=self.platform.send_message if config.delivery == "onebot" else None,
                upload_file=self.platform.upload_file if config.delivery == "onebot" else None,
                on_update=self.notify,
            )
        self.runners: dict[str, SceneRunner] = {}
        for scene, chat in self.chats.items():
            self.runners[scene] = SceneRunner(
                chat, lambda result, scene=scene: self._emit({"type": "turn", **result, "scene": scene}),
                resume=chat.restore(), ready_for_turn=self._ready,
            )

    def compacted(self, scene: str) -> None:
        if self.ingestor is not None:
            self.ingestor.request(scene)
        if self.learning is not None and scene in self.learning.scenes:
            state = self.learning.state(scene)
            latest = state["latest"]
            if (state["running"] and state["worker_error"] is None
                    and (latest is None or latest["status"] == "complete")):
                self.learning.request(scene)

    def notify(self) -> None:
        if self.on_update is not None:
            self.on_update()

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
        try:
            receipt = runner.receive_message(message, raw)
        except sqlite3.Error as error:
            self.storage_error = error
            self.stop()
            raise
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
                    if self.tasks is not None:
                        await self.tasks.close()
                finally:
                    try:
                        if self.learning is not None:
                            await self.learning.close()
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
