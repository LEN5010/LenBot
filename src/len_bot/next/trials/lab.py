"""Run from an isolated instance directory: python -m len_bot.next.trials.lab.

Default: array-form OneBot messages on stdin, simulated expressions on stdout.
Configured OneBot input runs until SIGINT/SIGTERM or a forward disconnect.
All runtime settings come from that directory's lenbot.config.json.
"""

import asyncio
import time
from contextlib import nullcontext
from pathlib import Path

from ..chat.session import Chat
from ..chat.attention import SceneRunner
from ..config import load_config
from ..instance_lock import instance_lock
from ..prompt_files import activate as activate_prompts
from ..models.client import ChatModel
from ..models.slots import ModelSlots
from ..models.limits import ModelBudget
from ..memory.service import open_memory
from ..memory.ingest import open_memory_ingestor
from ..learning.expression_selection import open_expression_service
from ..runtime.network import run_network
from ..persona.profile import load_persona
from ..storage.store import Store, encode
from ..platform.stdin_input import input_lines, parse_input


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
        budget = ModelBudget(config, store, None, root=config._instance_root)
        slots.admit = budget.check
        async with (
            ChatModel(config.model_settings("mind")) as mind,
            (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
             else nullcontext(None)) as vision,
            open_expression_service(config, store, slots=slots) as expression_service,
            open_memory(config, store, active_personas={config.scene: persona.id}, slots=slots) as memory,
            open_memory_ingestor(config, store, memory, [config.scene], slots=slots) as ingestor,
        ):
            budget.memory = memory
            if config.onebot is not None:
                await run_network(config, [(config, persona)], store, mind,
                                  vision=vision, memory=memory, ingestor=ingestor, slots=slots, budget=budget,
                                  expression_service=expression_service)
                return
            chat = Chat(config, persona, store, mind, vision=vision, memory=memory, now=now, slots=slots,
                        expression_service=expression_service,
                        on_compaction=None if ingestor is None else lambda: ingestor.request(config.scene))
            runner = SceneRunner(chat, lambda result: print(encode({"type": "turn", **result}), flush=True))
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(runner.run())
                async for line in input_lines():
                    try:
                        result = runner.receive(parse_input(line, config.bot_id))
                    except Exception as error:
                        result = {"status": "error", "error": f"{type(error).__name__}: {error}",
                                  "input_fragment": line[:500]}
                    print(encode({"type": "receipt", **result}), flush=True)
                runner.close_input()


def main() -> None:
    with instance_lock(Path.cwd()):
        activate_prompts(Path.cwd())
        asyncio.run(run())


if __name__ == "__main__":
    main()
