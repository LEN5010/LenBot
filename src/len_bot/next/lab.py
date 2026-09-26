"""Run from an isolated instance directory: python -m len_bot.next.lab.

stdin: one array-form OneBot message object per line. stdout: simulated results.
All runtime settings come from that directory's lenbot.config.json.
"""

import asyncio
import json
import sys
from pathlib import Path

from .chat import Chat
from .config import load_config
from .model import ChatModel
from .persona import load_persona
from .store import Store, encode


async def run() -> None:
    config = load_config(Path.cwd())
    persona = load_persona(config.persona)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(config.model_settings("voice")) as voice:
            chat = Chat(config, persona, store, mind, voice)
            chat.restore()
            for line in sys.stdin:
                try:
                    raw = json.loads(line)
                    result = await chat.receive(raw)
                except Exception as error:
                    result = {"status": "error", "error": f"{type(error).__name__}: {error}"}
                print(encode(result), flush=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
