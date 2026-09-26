"""Run from an isolated instance directory: python -m len_bot.next.lab.

Default: array-form OneBot messages on stdin, simulated expressions on stdout.
Configured OneBot input runs until SIGINT/SIGTERM or a forward disconnect.
All runtime settings come from that directory's lenbot.config.json.
"""

import asyncio
import json
import os
import signal
import stat
import sys
from pathlib import Path

from .chat import Chat
from .attention import SceneRunner
from .config import LabConfig, OneBotForward, load_config
from .model import ChatModel
from .onebot import OneBot
from .persona import Persona, load_persona
from .store import Store, encode


async def input_lines():
    # Regular redirected files are immediately readable; pipes/TTYs need async IO.
    if stat.S_ISREG(os.fstat(sys.stdin.fileno()).st_mode):
        for line in sys.stdin:
            yield line
            await asyncio.sleep(0)
        return
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        pending = b""
        while chunk := await reader.read(65536):
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            for line in lines:
                yield line.decode("utf-8")
                await asyncio.sleep(0)
        if pending:
            yield pending.decode("utf-8")
    finally:
        transport.close()


async def run() -> None:
    config = load_config(Path.cwd())
    persona = load_persona(config.persona)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(config.model_settings("voice")) as voice:
            if config.onebot is not None:
                await run_network(config, persona, store, mind, voice)
                return
            chat = Chat(config, persona, store, mind, voice)
            resume = chat.restore()
            runner = SceneRunner(chat, lambda result: print(encode({"type": "turn", **result}), flush=True), resume=resume)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(runner.run())
                async for line in input_lines():
                    try:
                        result = runner.receive(json.loads(line))
                    except Exception as error:
                        result = {"status": "error", "error": f"{type(error).__name__}: {error}",
                                  "input_fragment": line[:500]}
                    print(encode({"type": "receipt", **result}), flush=True)
                runner.close_input()


async def run_network(config: LabConfig, persona: Persona, store: Store,
                      mind: ChatModel, voice: ChatModel) -> None:
    stopped = asyncio.Event()
    accepting = True

    def emit(result: dict) -> None:
        print(encode(result), flush=True)

    def stop() -> None:
        nonlocal accepting
        accepting = False
        stopped.set()

    def receive(raw: dict) -> None:
        if raw["post_type"] == "meta_event":
            return
        if not accepting:
            emit({"type": "receipt", "status": "not_accepted", "reason": "stopping"})
        elif raw["post_type"] == "message":
            emit({"type": "receipt", **runner.receive(raw, ignore_other_scenes=True)})
        else:
            emit({"type": "platform_event", "status": "unsupported", "post_type": raw["post_type"]})

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

    chat = Chat(config, persona, store, mind, voice,
                send_text=platform.send_text if config.delivery == "onebot" else None)
    runner = SceneRunner(chat, lambda result: emit({"type": "turn", **result}),
                         resume=chat.restore(), ready_for_turn=ready)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        async with asyncio.TaskGroup() as group:
            pending = []
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
                running = group.create_task(runner.run())
                pending.append(running)
                done, _ = await asyncio.wait({running, stopping, terminated}, return_when=asyncio.FIRST_COMPLETED)
                accepting = False
                reason = "signal" if stopping in done else "transport_closed" if terminated in done else "scene_stopped"
                emit({"type": "runtime", "status": "stopping", "reason": reason})
                stopped.set()
                runner.close_input()
                await running
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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
