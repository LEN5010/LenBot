"""Run from an isolated instance directory: python -m len_bot.next.lab.

stdin: one array-form OneBot message object per line. stdout: simulated results.
All runtime settings come from that directory's lenbot.config.json.
"""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

from .chat import Chat
from .attention import SceneRunner
from .config import load_config
from .model import ChatModel
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
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(config.model_settings("voice")) as voice:
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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
