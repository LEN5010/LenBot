"""Run the worker Gateway as its own service.

    python -m len_bot.services.worker_gateway

The deployment passes one configuration file; the Gateway's own token,
journal path and workspace root come from there, not from the root LenBot
configuration, because the Gateway is the component that outlives a LenBot
outage.  The process starts, reconciles what its journal still owes an
outcome, and serves until stopped.

Stopping it cancels the runs it is watching.  Those runs are stopped and their
termination confirmed before the process exits, so a shutdown does not leave a
container nobody can name.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from len_bot.services.worker_gateway.app import create_app
from len_bot.services.worker_gateway.config import GatewayConfig
from len_bot.services.worker_gateway.runner import ExecutionRunner
from len_bot.services.worker_gateway.store import GatewayStore

logger = logging.getLogger('len_bot.worker_gateway')


def load_config(path: str | Path) -> GatewayConfig:
    return GatewayConfig.model_validate_json(Path(path).read_text(encoding='utf-8'))


async def _sweep_periodically(runner: ExecutionRunner, interval: float, stop: asyncio.Event) -> None:
    """Keep re-checking stored unfinished executions on the Gateway's own clock.

    A run is stopped on its stored deadline whether or not any watcher survived
    a restart, so "the control service is down" is not a reason for a container
    to keep running.
    """
    while not stop.is_set():
        try:
            await runner.sweep()
        except Exception as error:  # a failed pass is reported, not hidden
            logger.error('执行核对失败：%s', error)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def serve(config: GatewayConfig) -> None:
    store = GatewayStore(config.database_path)
    await store.initialize()
    root = Path(config.database_path).resolve().parent
    runner = ExecutionRunner(config, store,
                             workspaces_root=Path(config.workspaces_root).expanduser(),
                             controls_root=root / 'controls')
    # The app's own startup hook reconciles and its shutdown hook stops the
    # runs, so the same behaviour applies however the ASGI app is served.
    app = create_app(config, store, runner)
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port,
                                           log_level='info', access_log=False))
    stop = asyncio.Event()
    sweeper = asyncio.create_task(_sweep_periodically(runner, config.sweep_interval_seconds, stop))
    try:
        await server.serve()
    finally:
        stop.set()
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
        await store.close()


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description='LenBot Worker Gateway')
    parser.add_argument('--config', required=True, help='网关自己的配置文件路径')
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config(arguments.config)
    except (OSError, ValidationError) as error:
        raise SystemExit(f'网关配置不可用：{error}') from None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(config))


if __name__ == '__main__':
    main()
