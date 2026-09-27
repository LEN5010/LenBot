"""One task's configured container, model bridge, and Pi RPC lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Any, Literal

from .model import ModelSettings
from .model_slots import ModelSlots
from .pi_rpc import PiRpc
from .pricing import ModelPrice
from .sandbox import DockerSandbox, SandboxHandle
from .worker_model import Limits, WorkerModelProxy
from .worker_transport import WorkerTransport


_PI_PROVIDER = "lenbot-task"


@dataclass(frozen=True, slots=True)
class WorkerSession:
    sandbox: SandboxHandle
    pi: PiRpc
    bridge: WorkerTransport
    proxy: WorkerModelProxy


def _write_json(path: Path, body: dict[str, Any]) -> None:
    """Replace one task-generated Pi file without following an old file symlink."""
    data = (json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _configure_pi(handle: SandboxHandle, *, token: str, port: int,
                  settings: ModelSettings, context_window_tokens: int,
                  model_reasoning: bool, input_support: Literal["text", "text-image"]) -> None:
    agent_dir = handle.home / ".pi" / "agent"
    # Stored credentials outrank models.json in Pi. A retained task home may
    # contain old credentials, but this run has exactly one newly issued token.
    (agent_dir / "auth.json").unlink(missing_ok=True)
    inputs = ["text"] if input_support == "text" else ["text", "image"]
    _write_json(agent_dir / "models.json", {
        "providers": {
            _PI_PROVIDER: {
                "api": "openai-completions",
                "baseUrl": f"http://127.0.0.1:{port}/v1",
                "apiKey": token,
                "models": [{
                    "id": settings.model,
                    "reasoning": model_reasoning,
                    "input": inputs,
                    "contextWindow": context_window_tokens,
                    "maxTokens": settings.max_output_tokens,
                    "compat": {
                        "supportsStore": False,
                        "supportsUsageInStreaming": True,
                        "supportsFinishReason": True,
                        "maxTokensField": "max_completion_tokens",
                        "supportsLongCacheRetention": False,
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": False,
                    },
                }],
            },
        },
    })
    _write_json(agent_dir / "settings.json", {
        "defaultProvider": _PI_PROVIDER,
        "defaultModel": settings.model,
        "enabledModels": [f"{_PI_PROVIDER}/{settings.model}"],
        "defaultThinkingLevel": "off",
        "retry": {"enabled": False, "provider": {"maxRetries": 0}},
        "compaction": {"enabled": False},
        "cacheWarming": "off",
        "packages": [],
        "extensions": [],
    })


async def _cleanup(sandbox: DockerSandbox, handle: SandboxHandle | None,
                   pi: PiRpc | None, bridge: WorkerTransport | None,
                   proxy: WorkerModelProxy) -> None:
    failures: list[tuple[str, BaseException]] = []
    for name, action in (
        ("model proxy", proxy.close),
        ("Pi RPC", None if pi is None else pi.close),
        ("model bridge", None if bridge is None else bridge.close),
        ("task container", None if handle is None else lambda: sandbox.stop(handle)),
    ):
        if action is None:
            continue
        try:
            await action()
        except BaseException as error:
            failures.append((name, error))
    if handle is not None:
        try:
            (handle.home / ".pi" / "agent" / "models.json").unlink(missing_ok=True)
        except BaseException as error:
            failures.append(("task token file", error))
    if failures:
        name, first = failures[0]
        first.add_note(f"Cleanup failed at {name}")
        for name, error in failures[1:]:
            first.add_note(f"Cleanup also failed at {name}: {type(error).__name__}: {error}")
        raise first


@asynccontextmanager
async def worker_session(
    sandbox: DockerSandbox,
    *,
    scene: str,
    task_id: str,
    settings: ModelSettings,
    provider: str,
    context_window_tokens: int,
    price: ModelPrice | None,
    limits: Limits,
    model_reasoning: bool,
    start_call: Callable[[dict[str, Any]], int],
    finish_call: Callable[[int, dict[str, Any]], None],
    input_support: Literal["text", "text-image"] = "text",
    slots: ModelSlots | None = None,
) -> AsyncIterator[WorkerSession]:
    """Start one network-isolated task with host-mediated model access.

    The caller consumes Pi events and bridge failures concurrently. A yielded
    session is not a task-success decision and never starts public egress.
    Closing retains task files while stopping every live part.
    """
    token = secrets.token_urlsafe(32)
    proxy = WorkerModelProxy(
        settings, provider, context_window_tokens, price, token, limits,
        slots=slots, start_call=start_call, finish_call=finish_call,
    )
    handle: SandboxHandle | None = None
    bridge: WorkerTransport | None = None
    pi: PiRpc | None = None
    original: BaseException | None = None
    try:
        await proxy.__aenter__()
        handle = await sandbox.ensure(scene, task_id)
        bridge = await sandbox.spawn_model_bridge(
            handle, proxy=proxy, stderr_path=handle.workspace / "model-bridge.stderr"
        )
        _configure_pi(
            handle, token=token, port=bridge.port, settings=settings,
            context_window_tokens=context_window_tokens,
            model_reasoning=model_reasoning, input_support=input_support,
        )
        pi = await sandbox.spawn_pi(
            handle, provider=_PI_PROVIDER, model=settings.model,
            stderr_path=handle.workspace / "pi.stderr",
        )
        yield WorkerSession(handle, pi, bridge, proxy)
    except BaseException as error:
        original = error
        raise
    finally:
        cleaning = asyncio.create_task(_cleanup(sandbox, handle, pi, bridge, proxy))
        interruption: asyncio.CancelledError | None = None
        while not cleaning.done():
            try:
                await asyncio.shield(cleaning)
            except asyncio.CancelledError as error:
                interruption = error
            except BaseException:
                break
        cleanup_error: BaseException | None = None
        try:
            cleaning.result()
        except BaseException as error:
            cleanup_error = error
        if original is not None:
            if cleanup_error is not None:
                original.add_note(f"Worker session cleanup also failed: {type(cleanup_error).__name__}: "
                                  f"{cleanup_error}")
            if interruption is not None:
                original.add_note("Worker session cleanup was cancelled; cleanup still finished")
        elif cleanup_error is not None:
            raise cleanup_error
        elif interruption is not None:
            raise interruption
