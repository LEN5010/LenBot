"""Shared scene execution behind one OneBot connection or explicit simulated stdin."""

from __future__ import annotations

import asyncio
import signal
import logging
import sqlite3
from collections.abc import Callable
from collections import deque

from ..chat.attention import SceneRunner
from ..media.audio import AudioService
from ..chat.session import Chat
from ..chat.tools import tool_catalog
from ..tools.skills import select_skills
from ..config import LabConfig, SharedConfig
from ..configuration.onebot import OneBotForward
from .operations import credentials, redact, redact_record
from ..platform.messages import parse_message, parse_notice
from ..models.client import ChatModel
from ..models.slots import ModelSlots
from ..models.limits import ModelBudget
from .retention import Retention
from ..memory.service import MemoryService
from ..memory.ingest import MemoryIngestor
from ..learning.expressions import ExpressionLearner
from ..learning.jargon import JargonLearner
from ..learning.sticker_collection import StickerCollector
from ..learning.reply_effects import ReplyEffectTracker
from ..learning.expression_selection import ExpressionService
from ..platform.onebot import OneBot, OneBotCallError
from ..persona.profile import Persona
from ..plugins.host import PluginHost
from ..tools.mcp_host import MCPHost
from ..storage.store import Store, encode
from ..work.service import WorkTasks


class NetworkRuntime:
    def __init__(self, config: SharedConfig, scene_configs: list[tuple[LabConfig, Persona]],
                 store: Store, mind: ChatModel, *,
                 vision: ChatModel | None = None, slots: ModelSlots | None = None,
                 memory: MemoryService | None = None,
                 budget: ModelBudget | None = None,
                 ingestor: MemoryIngestor | None = None,
                 tasks: WorkTasks | None = None,
                 learning: ExpressionLearner | None = None,
                 jargon: JargonLearner | None = None,
                 sticker_collection: StickerCollector | None = None,
                 reply_effects: ReplyEffectTracker | None = None,
                 expression_service: ExpressionService | None = None,
                 plugins: PluginHost | None = None,
                 mcp: MCPHost | None = None,
                 on_update: Callable[[], None] | None = None):
        self.config, self.store = config, store
        self.log_secrets = credentials(config)
        self.logs: deque[dict] = deque(maxlen=500)
        self.memory = memory
        self.budget = ModelBudget(config, store, memory, root=config._instance_root) if budget is None else budget
        if slots is None and (config.limits.daily_model_cost is not None or config.limits.scene_daily_model_cost):
            raise ValueError("配置模型金额预算时必须装配共享ModelSlots")
        if slots is not None:
            slots.admit = self.budget.check
        self.slots = slots
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
        self.mcp = mcp
        if plugins is not None:
            plugins.on_update = self.notify
        self.on_update = on_update
        self.status = "created"
        self.last_platform_error: str | None = None
        self.last_runtime_error: str | None = None
        self.stopped = asyncio.Event()
        self.connection_requested = asyncio.Event()
        self.retention = Retention(self)
        self.accepting = config.onebot is None
        self.storage_error: sqlite3.Error | None = None
        self.platform = (None if config.onebot is None else OneBot(
            config.onebot, bot_qq=config.bot_qq, on_event=self._receive,
            on_error=self._platform_error, on_connection_change=self._connection_changed))
        self.audio = AudioService(store, {cfg.scene: cfg for cfg, _ in scene_configs},
                                  self.platform.call if config.delivery == "onebot" else None, slots,
                                  self.audio_updated)
        if tasks is not None:
            tasks.audio = self.audio
        self.chats: dict[str, Chat] = {}
        for scene_config, persona in scene_configs:
            scene = scene_config.scene
            if scene in self.chats:
                raise ValueError(f"Duplicate network scene {scene}")
            self.chats[scene] = Chat(
                scene_config, persona, store, mind, vision=vision, slots=slots, memory=memory,
                tasks=tasks, expression_service=expression_service,
                on_compaction=lambda scene=scene: self.compacted(scene),
                on_reply_sample=(None if reply_effects is None else
                                 lambda scene=scene: reply_effects.wake(scene)),
                send_message=self.platform.send_message if config.delivery == "onebot" else None,
                upload_file=self.platform.upload_file if config.delivery == "onebot" else None,
                platform_call=self.platform.call if config.delivery == "onebot" else None,
                external_tools=(([] if plugins is None else plugins.tools_for(scene, preparing=True))
                                + ([] if mcp is None else mcp.tools_for(scene))),
                audio_service=self.audio,
                on_update=self.notify,
            )
        self.runners: dict[str, SceneRunner] = {}
        for scene, chat in self.chats.items():
            self.runners[scene] = SceneRunner(
                chat, lambda result, scene=scene: self._emit({"type": "turn", **result, "scene": scene}),
                ready_for_turn=self._ready,
                connected_since=lambda: self.connected_since,
            )
        if plugins is not None:
            plugins.bind(self)
        if mcp is not None:
            mcp.on_update = self.refresh_external_tools

    def refresh_external_tools(self) -> None:
        if self.mcp is not None:
            self.mcp.reserved = {tool['function']['name'] for tool in tool_catalog(platform=True)} | (
                set() if self.plugins is None else set(self.plugins.tool_owner))
        for scene, chat in self.chats.items():
            if self.tasks is not None:
                catalog = tuple(skill for skill in chat.skills if skill.source != 'plugin') + (
                    () if self.plugins is None else self.plugins.skills_for(scene))
                chat.skills = select_skills(catalog, chat.persona.skills)
                self.tasks.skills[scene] = chat.skills
            chat.set_external_tools(([] if self.plugins is None else self.plugins.tools_for(scene, preparing=self.status != "running"))
                                    + ([] if self.mcp is None else self.mcp.tools_for(scene)))
        self.notify()

    def audio_updated(self, scene: str) -> None:
        self.runners[scene].changed.set()
        self.notify()

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
        self.accepting = self.status == "running" and self.platform.connected
        self.notify()

    def _status(self, status: str) -> None:
        if self.status != status:
            self.status = status
            self.notify()

    def _emit(self, result: dict) -> None:
        if self.platform is None:
            result = {**result, 'input_source': 'stdin'}
        def clean_value(text):
            if self.plugins is not None:
                for name in self.plugins.plugins:
                    text = self.plugins.redact(name, text)
            return redact(text, self.log_secrets)
        clean = redact_record(result, clean_value)
        text = encode(clean)
        self.logs.append({"time": self.store.now(), "record": clean})
        summary = {key: clean[key] for key in (
            "type", "status", "scene", "turn_id", "error", "reason", "post_type", "notice_type",
            "plugin_handlers", "platform_message_id", "delivery", "quiet_until", "pending_wake",
        ) if key in clean}
        logging.getLogger(__name__).log(logging.ERROR if result.get("error") else logging.INFO,
                                       "%s", encode(summary))
        print(text, flush=True)
        self.notify()

    def _platform_error(self, error: str) -> None:
        self.last_platform_error = error
        self._emit({"type": "platform_error", "error": error})

    def stop(self) -> None:
        self.accepting = False
        if self.tasks is not None:
            self.tasks.stop()
        self.stopped.set()
        for runner in self.runners.values():
            runner.close_input()
        if self.status != "stopped":
            self._status("stopping")

    def _receive(self, raw: dict) -> None:
        post_type = raw["post_type"]
        if post_type == "meta_event":
            return
        if post_type != "message":
            if not self.accepting:
                self._emit({"type": "receipt", "status": "not_accepted", "reason": self.status})
            elif post_type == "notice":
                try:
                    notice = parse_notice(raw)
                    if notice is None or notice.scene not in self.runners:
                        self._emit({"type": "platform_event", "status": "ignored", "post_type": post_type})
                        return
                    self.store.save_notice(notice)
                    handled = 0 if self.plugins is None else self.plugins.handle_notice(notice)
                except sqlite3.Error as error:
                    self.storage_error = error
                    self.stop()
                    raise
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
                        "status": "not_accepted", "reason": self.status})
            return
        runner = self.runners.get(message.scene)
        if runner is None:
            self._emit({"type": "receipt", "scene": message.scene, "status": "ignored",
                        "platform_message_id": message.platform_message_id})
            return
        blocked = message.sender.uid in runner.config.permissions.blacklist
        matched = (None if self.plugins is None or blocked else
                   self.plugins.match_message(message, tuple(runner.settings.other_bot_qqs)))
        claim = (None if matched is None else
                 (matched.record.name, self.plugins.message_report(message, matched, "已接管，处理尚未结束。")))
        try:
            receipt = runner.receive_message(message, raw, wake=matched is None, plugin_claim=claim)
        except sqlite3.Error as error:
            self.storage_error = error
            self.stop()
            raise
        if (self.reply_effects is not None and receipt["status"] != "duplicate" and not message.is_self):
            self.reply_effects.wake(message.scene)
        if (self.sticker_collection is not None and message.scene in self.sticker_collection.scenes
                and message.scene not in self.sticker_collection.errors
                and receipt["status"] != "duplicate" and not message.is_self and not blocked
                and any(segment.type == "image" for segment in message.segments)):
            self.sticker_collection.request(message.scene)
        if matched is not None and receipt["status"] != "duplicate":
            self.plugins.dispatch_message(message, matched)
            receipt["plugin_handler"] = f"{matched.record.name} {matched.label}"
        if receipt["status"] != "duplicate":
            self.audio.request(message.scene)
        self._emit({"type": "receipt", **receipt, "scene": message.scene})

    async def _ready(self, wait: bool) -> bool:
        if self.stopped.is_set():
            return False
        if self.platform is None:
            return True
        if self.platform.connected:
            return True
        if not wait:
            return False
        if isinstance(self.config.onebot, OneBotForward):
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

    @property
    def can_connect(self) -> bool:
        return self.status == "connection_failed" and not self.stopped.is_set()

    def request_connection(self) -> None:
        if not self.can_connect:
            raise RuntimeError("只有首次连接失败且业务尚未启动时可以手动连接；其他停止状态请重启宿主")
        self._status("starting")
        self.connection_requested.set()

    async def _start_platform(self, *, manual_connection: bool) -> bool:
        """Before starting business services, permit only explicit connection attempts."""
        while not self.stopped.is_set():
            self._status("starting")
            starting = asyncio.create_task(self.platform.start())
            stopping = asyncio.create_task(self.stopped.wait())
            requested: asyncio.Task | None = None
            observed = False
            try:
                done, _ = await asyncio.wait({starting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    return False
                try:
                    observed = True
                    await starting
                except OneBotCallError as error:
                    self._platform_error(f"{type(error).__name__}: {error}")
                    if not manual_connection:
                        raise
                    self._status("connection_failed")
                    requested = asyncio.create_task(self.connection_requested.wait())
                    await asyncio.wait({requested, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    self.connection_requested.clear()
                else:
                    self.last_platform_error = None
                    self.notify()
                    return True
            finally:
                waiting = [starting, stopping] + ([] if requested is None else [requested])
                for task in waiting:
                    task.cancel()
                results = await asyncio.gather(*waiting, return_exceptions=True)
                # Cancellation can itself fail while OneBot releases a partial
                # connection. Do not turn that unobserved cleanup failure into
                # a clean stop or a reconnectable transport.
                if (not observed and isinstance(results[0], BaseException)
                        and not isinstance(results[0], asyncio.CancelledError)):
                    raise results[0]
        return False

    async def run(self, *, manage_signals: bool = True, manual_connection: bool = False) -> None:
        if self.platform is None:
            from .stdin_host import run_stdin
            await run_stdin(self, manage_signals=manage_signals)
            return
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
            if not await self._start_platform(manual_connection=manual_connection):
                return
            async with asyncio.TaskGroup() as group:
                pending: list[asyncio.Task] = []
                try:
                    stopping = group.create_task(self.stopped.wait())
                    pending.append(stopping)
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
                    self.audio.start()
                    if self.jargon is not None:
                        self.jargon.start()
                    if self.sticker_collection is not None:
                        self.sticker_collection.start()
                    if self.reply_effects is not None:
                        self.reply_effects.start()
                    if self.plugins is not None:
                        await self.plugins.start()
                    self.refresh_external_tools()
                    self.accepting = self.platform.connected
                    self._status("running")
                    self._emit({"type": "runtime", "status": "ready", "input": "onebot",
                                "delivery": self.config.delivery})
                    pending.append(group.create_task(self.retention.run()))
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
                    await self.audio.close()
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
                                    try:
                                        if self.mcp is not None:
                                            await self.mcp.close()
                                    finally:
                                        try:
                                            await self.audio.close()
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
                      store: Store, mind: ChatModel, *,
                      vision: ChatModel | None = None, slots: ModelSlots | None = None,
                      memory: MemoryService | None = None, ingestor: MemoryIngestor | None = None,
                      expression_service: ExpressionService | None = None, budget: ModelBudget | None = None) -> None:
    runtime = NetworkRuntime(config, scene_configs, store, mind, vision=vision, slots=slots,
                             memory=memory, ingestor=ingestor, expression_service=expression_service, budget=budget)
    await runtime.run()
