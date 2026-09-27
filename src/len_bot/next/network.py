"""One OneBot connection for explicitly configured isolated chat scenes."""

from __future__ import annotations

import asyncio
import signal
import sqlite3

from .attention import SceneRunner
from .chat import Chat
from .config import LabConfig, OneBotForward, SharedConfig
from .messages import parse_message
from .model import ChatModel
from .model_slots import ModelSlots
from .onebot import OneBot
from .persona import Persona
from .store import Store, encode


async def run_network(config: SharedConfig, scene_configs: list[tuple[LabConfig, Persona]],
                      store: Store, mind: ChatModel, voice: ChatModel, *,
                      vision: ChatModel | None = None, slots: ModelSlots | None = None) -> None:
    if config.onebot is None:
        raise ValueError("Network input requires OneBot configuration")
    stopped = asyncio.Event()
    accepting = True
    storage_error: sqlite3.Error | None = None

    def emit(result: dict) -> None:
        print(encode(result), flush=True)

    def stop() -> None:
        nonlocal accepting
        accepting = False
        stopped.set()

    runners: dict[str, SceneRunner] = {}

    def receive(raw: dict) -> None:
        nonlocal storage_error
        post_type = raw["post_type"]
        if post_type == "meta_event":
            return
        if post_type != "message":
            if not accepting:
                emit({"type": "receipt", "status": "not_accepted", "reason": "stopping"})
            else:
                emit({"type": "platform_event", "status": "unsupported", "post_type": post_type})
            return
        message = parse_message(raw, own_message_ids=set())
        if not accepting:
            emit({"type": "receipt", "scene": message.scene,
                  "status": "not_accepted", "reason": "stopping"})
            return
        runner = runners.get(message.scene)
        if runner is None:
            emit({"type": "receipt", "scene": message.scene, "status": "ignored",
                  "platform_message_id": message.platform_message_id})
            return
        try:
            receipt = runner.receive_message(message, raw)
        except sqlite3.Error as error:
            storage_error = error
            stop()
            raise
        emit({"type": "receipt", **receipt, "scene": message.scene})

    platform = OneBot(config.onebot, bot_qq=config.bot_qq, on_event=receive,
                      on_error=lambda error: emit({"type": "platform_error", "error": error}))

    async def ready(wait: bool) -> bool:
        if platform.connected:
            return True
        if not wait:
            return False
        if stopped.is_set() or isinstance(config.onebot, OneBotForward):
            return False
        async with asyncio.TaskGroup() as waiting:
            connected = waiting.create_task(platform.wait_connected(None))
            stopping = waiting.create_task(stopped.wait())
            try:
                await asyncio.wait({connected, stopping}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                connected.cancel()
                stopping.cancel()
        return platform.connected

    chats: dict[str, Chat] = {}
    for scene_config, persona in scene_configs:
        scene = scene_config.scene
        if scene in chats:
            raise ValueError(f"Duplicate network scene {scene}")
        chats[scene] = Chat(scene_config, persona, store, mind, voice, vision=vision, slots=slots,
                            send_text=platform.send_text if config.delivery == "onebot" else None)
    for scene, chat in chats.items():
        runners[scene] = SceneRunner(
            chat, lambda result, scene=scene: emit({"type": "turn", **result, "scene": scene}),
            resume=chat.restore(), ready_for_turn=ready,
        )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        async with asyncio.TaskGroup() as group:
            pending: list[asyncio.Task] = []
            try:
                stopping = group.create_task(stopped.wait())
                starting = group.create_task(platform.start())
                pending.extend([stopping, starting])
                done, _ = await asyncio.wait({starting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    return
                await starting
                emit({"type": "runtime", "status": "started", "input": "onebot",
                      "delivery": config.delivery, "addresses": platform.addresses})
                terminated = group.create_task(platform.wait_terminated())
                connected = group.create_task(platform.wait_connected(None))
                pending.extend([terminated, connected])
                done, _ = await asyncio.wait({connected, stopping, terminated}, return_when=asyncio.FIRST_COMPLETED)
                if connected not in done or stopping in done or terminated in done:
                    return
                await connected
                emit({"type": "runtime", "status": "ready", "input": "onebot", "delivery": config.delivery})
                running = [group.create_task(runner.run()) for runner in runners.values()]
                pending.extend(running)
                done, _ = await asyncio.wait({*running, stopping, terminated}, return_when=asyncio.FIRST_COMPLETED)
                accepting = False
                reason = ("storage_error" if storage_error is not None else
                          "signal" if stopping in done else "transport_closed" if terminated in done
                          else "scene_stopped")
                emit({"type": "runtime", "status": "stopping", "reason": reason})
                stopped.set()
                for runner in runners.values():
                    runner.close_input()
                await asyncio.gather(*running)
            finally:
                accepting = False
                for task in pending:
                    task.cancel()
    finally:
        try:
            await platform.close()
            emit({"type": "runtime", "status": "stopped"})
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        if storage_error is not None:
            raise storage_error
