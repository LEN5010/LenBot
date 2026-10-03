"""Simulated native input for the same multi-scene host and actual work-task services."""

from __future__ import annotations

import asyncio
import signal
import sqlite3
from typing import TYPE_CHECKING

from ..platform.stdin_input import input_lines, parse_input

if TYPE_CHECKING:
    from .network import NetworkRuntime


async def run_stdin(runtime: NetworkRuntime, *, manage_signals: bool) -> None:
    if runtime.stopped.is_set():
        runtime._status('stopped')
        return
    runtime._status('starting')
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    original: BaseException | None = None
    try:
        if manage_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, runtime.stop)
                installed.append(sig)
        if runtime.tasks is not None:
            await runtime.tasks.recover()
            await runtime.tasks.start()
        for service in (runtime.learning, runtime.jargon, runtime.sticker_collection, runtime.reply_effects):
            if service is not None:
                service.start()
        runtime.audio.start()
        runtime.refresh_external_tools()
        runtime._status('running')
        runtime._emit({'type': 'runtime', 'status': 'ready', 'input': 'stdin', 'delivery': 'simulated',
                       'scenes': list(runtime.runners),
                       'notice': 'No OneBot connection or platform outlet. Models and explicitly enabled task '
                                 'containers/public egress still execute normally and may cost money.'})
        async with asyncio.TaskGroup() as group:
            pending: list[asyncio.Task] = []
            try:
                running = [group.create_task(runner.run()) for runner in runtime.runners.values()]
                pending.extend(running)

                async def feed() -> None:
                    async for line in input_lines():
                        try:
                            runtime._receive(parse_input(line, runtime.config.bot_qq))
                        except sqlite3.Error:
                            raise
                        except Exception as error:
                            runtime._emit({'type': 'receipt', 'status': 'error',
                                           'error': f'{type(error).__name__}: {error}', 'input_fragment': line[:500]})
                    # As in the lab, EOF first drains already accepted scene work.
                    # It does not promise to finish a task that still needs input.
                    for runner in runtime.runners.values():
                        runner.close_input()
                    await asyncio.gather(*running)

                feeding = group.create_task(feed())
                stopping = group.create_task(runtime.stopped.wait())
                pending.extend([feeding, stopping, group.create_task(runtime.retention.run())])
                if runtime.tasks is not None:
                    pending.append(group.create_task(runtime.tasks.wait_failure()))
                done, _ = await asyncio.wait({feeding, stopping}, return_when=asyncio.FIRST_COMPLETED)
                runtime._emit({'type': 'runtime', 'status': 'stopping',
                               'reason': 'signal' if stopping in done else 'stdin_eof'})
            finally:
                runtime.stop()
                for runner in runtime.runners.values():
                    runner.close_input()
                for task in pending:
                    task.cancel()
    except BaseException as error:
        original = error
        raise
    finally:
        runtime.stop()
        failures: list[BaseException] = []
        # Each cleanup owns its own error; still close the other live units.
        for name, service in (('tasks', runtime.tasks), ('learning', runtime.learning),
                              ('jargon', runtime.jargon), ('stickers', runtime.sticker_collection),
                              ('reply effects', runtime.reply_effects), ('audio', runtime.audio),
                              ('MCP', runtime.mcp)):
            if service is None:
                continue
            try:
                await service.close()
            except BaseException as error:
                error.add_note(f'stdin host cleanup: {name}')
                failures.append(error)
        for sig in installed:
            loop.remove_signal_handler(sig)
        runtime._status('stopped')
        try:
            runtime._emit({'type': 'runtime', 'status': 'stopped'})
        except Exception as error:
            failures.append(error)
        if failures:
            cleaning = BaseExceptionGroup('stdin host cleanup failed', failures)
            if original is not None:
                raise original from cleaning
            raise cleaning
        if runtime.storage_error is not None:
            raise runtime.storage_error
