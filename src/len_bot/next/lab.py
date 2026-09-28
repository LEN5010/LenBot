"""Run from an isolated instance directory: python -m len_bot.next.lab.

Default: array-form OneBot messages on stdin, simulated expressions on stdout.
Configured OneBot input runs until SIGINT/SIGTERM or a forward disconnect.
All runtime settings come from that directory's lenbot.config.json.
"""

import asyncio
import json
import os
import stat
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from .chat import Chat
from .attention import SceneRunner
from .config import load_config
from .model import ChatModel
from .model_slots import ModelSlots
from .limits import ModelBudget
from .memory import open_memory
from .memory_ingest import open_memory_ingestor
from .expression_selection import open_expression_service
from .network import run_network
from .persona import load_persona
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
    now = time.time
    if config.replay_clock is not None:
        epoch = config.replay_clock.epoch
        origin = config.replay_clock.monotonic_origin

        def now() -> float:
            return epoch + (time.monotonic() - origin)

    with Store(config.database, now=now) as store:
        slots = ModelSlots(config.max_model_requests)
        budget = ModelBudget(config, store, None)
        slots.admit = budget.check
        async with (
            ChatModel(config.model_settings("mind")) as mind,
            ChatModel(config.model_settings("voice")) as voice,
            (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
             else nullcontext(None)) as vision,
            open_expression_service(config, store, slots=slots) as expression_service,
            open_memory(config, store, slots=slots) as memory,
            open_memory_ingestor(config, store, memory, [config.scene], slots=slots) as ingestor,
        ):
            budget.memory = memory
            if expression_service is not None:
                for scene in expression_service.scenes:
                    expression_service.validate(scene)
            if config.onebot is not None:
                await run_network(config, [(config, persona)], store, mind, voice,
                                  vision=vision, memory=memory, ingestor=ingestor, slots=slots, budget=budget,
                                  expression_service=expression_service)
                return
            chat = Chat(config, persona, store, mind, voice, vision=vision, memory=memory, now=now, slots=slots,
                        expression_service=expression_service,
                        on_compaction=None if ingestor is None else lambda: ingestor.request(config.scene))
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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
