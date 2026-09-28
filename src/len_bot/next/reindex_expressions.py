"""Explicit offline rebuild using this directory's sole root configuration."""

import asyncio
from pathlib import Path
import sys

from .config import load_instance_config
from .expression_selection import open_expression_service
from .store import Store, encode
from .model_slots import ModelSlots
from .limits import ModelBudget


async def rebuild() -> None:
    config = load_instance_config(Path.cwd())
    with Store(config.database) as store:
        slots = ModelSlots(config.max_model_requests)
        slots.admit = ModelBudget(config, store, None, root=config._instance_root).check
        async with open_expression_service(config, store, slots=slots) as service:
            if service is None:
                raise ValueError("No configured expression embedding backend to rebuild")
            for scene in service.scenes:
                count = await service.rebuild(scene)
                print(encode({"scene": scene, "indexed_expressions": count}), flush=True)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Expression reindex takes no arguments; stop the host and run from its configured directory")
    asyncio.run(rebuild())


if __name__ == "__main__":
    main()
