"""Run explicit isolated scenes behind one configured OneBot connection."""

import asyncio
from contextlib import contextmanager, nullcontext
from pathlib import Path
import signal

import uvicorn

from .config import load_host_config
from .host_panel import create_app
from .model import ChatModel
from .model_slots import ModelSlots
from .memory import open_memory
from .memory_ingest import open_memory_ingestor
from .network import NetworkRuntime
from .persona import load_persona
from .store import Store


class HostPanelServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The host coordinates the HTTP server and the OneBot runtime.
        yield


async def run_with_panel(runtime: NetworkRuntime, server: HostPanelServer) -> None:
    def stop() -> None:
        runtime.stop()
        server.should_exit = True

    async def serve() -> None:
        try:
            await server.serve()
        except SystemExit as error:
            # Uvicorn reports bind failures with SystemExit; preserve its cause
            # while allowing the peer runtime to finish its shutdown path.
            raise RuntimeError(f"Panel server exited: {error}") from error

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    tasks = [asyncio.create_task(runtime.run(manage_signals=False)), asyncio.create_task(serve())]
    errors: list[BaseException] = []
    try:
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except BaseException as error:
            errors.append(error)
        finally:
            stop()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        errors.extend(result for result in results if isinstance(result, BaseException))
        if errors:
            raise BaseExceptionGroup("Multi-scene host stopped with errors", errors)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def run() -> None:
    config = load_host_config(Path.cwd())
    personas = {path: load_persona(path)
                for path in dict.fromkeys(settings.persona for settings in config.scenes.values())}
    scenes = [(config.scene_config(scene), personas[settings.persona])
              for scene, settings in config.scenes.items()]
    slots = ModelSlots(config.max_model_requests)
    with Store(config.database) as store:
        async with (
            ChatModel(config.model_settings("mind")) as mind,
            ChatModel(config.model_settings("voice")) as voice,
            (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
             else nullcontext(None)) as vision,
            open_memory(config) as memory,
            open_memory_ingestor(config, store, memory, list(config.scenes), slots=slots) as ingestor,
        ):
            runtime = NetworkRuntime(config, scenes, store, mind, voice, vision=vision, slots=slots,
                                     memory=memory, ingestor=ingestor)
            if config.panel is None:
                await runtime.run()
            else:
                app = create_app(config, runtime, root=Path.cwd())
                server = HostPanelServer(uvicorn.Config(
                    app, host=config.panel.host, port=config.panel.port,
                ))
                await run_with_panel(runtime, server)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
