"""Run explicit isolated scenes behind one configured OneBot connection."""

import asyncio
from contextlib import nullcontext
from pathlib import Path

from .config import load_host_config
from .model import ChatModel
from .model_slots import ModelSlots
from .network import run_network
from .persona import load_persona
from .store import Store


async def run() -> None:
    config = load_host_config(Path.cwd())
    scenes = [(config.scene_config(scene), load_persona(settings.persona))
              for scene, settings in config.scenes.items()]
    slots = ModelSlots(config.max_model_requests)
    with Store(config.database) as store:
        async with (
            ChatModel(config.model_settings("mind")) as mind,
            ChatModel(config.model_settings("voice")) as voice,
            (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
             else nullcontext(None)) as vision,
        ):
            await run_network(config, scenes, store, mind, voice, vision=vision, slots=slots)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
