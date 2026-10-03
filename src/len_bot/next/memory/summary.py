"""Generate derived L0/L1 summaries for one local memory directory with the memory model."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..chat.recap import ContextBudgetError, estimate_request
from .jobs import MemoryJobs
from .local import ABSTRACT_CHARS, OVERVIEW_CHARS, LocalMemory
from ..models.client import ChatModel, ModelProtocolError, ModelReply
from ..models.slots import ModelSlots
from ..models.pricing import estimate_cost
from ..storage.store import encode

if TYPE_CHECKING:
    from ..config import SharedConfig


PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "next_memory_summary.md"


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def parse_summary(reply: ModelReply) -> tuple[str, str]:
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("memory summary returned tool calls without tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {"abstract", "overview"}:
            raise ValueError("expected an object containing only abstract and overview")
        abstract, overview = value["abstract"], value["overview"]
        if not isinstance(abstract, str) or not abstract.strip() or not isinstance(overview, str) or not overview.strip():
            raise ValueError("abstract and overview must be nonblank text")
        if len(abstract) > ABSTRACT_CHARS or len(overview) > OVERVIEW_CHARS:
            raise ValueError(f"abstract/overview exceed {ABSTRACT_CHARS}/{OVERVIEW_CHARS} characters: "
                             f"{len(abstract)}/{len(overview)}")
        return abstract, overview
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid memory summary response: {error}; response fragment: {fragment!r}") from error


def affected_directories(paths: list[str]) -> list[str]:
    """Directories of written files and their ancestors, deepest first."""
    directories: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        directories.update("/".join(parts[:depth]) for depth in range(len(parts) + 1))
    return sorted(directories, key=lambda item: (-(item.count("/") + bool(item)), item))


class MemorySummarizer:
    def __init__(self, config: SharedConfig, backend: LocalMemory, jobs: MemoryJobs, model: ChatModel, *,
                 slots: ModelSlots | None = None):
        self.config = config
        self.backend = backend
        self.jobs = jobs
        self.model = model
        self.slots = slots
        self._prompt = PROMPT.read_text(encoding="utf-8")
        jobs.recover_summaries()

    async def summarize(self, scene: str, path: str, *,
                        scope: Literal["scene", "public"] = "scene") -> dict:
        """Summarize one directory; the caller holds the partition's memory write lock."""
        inputs = await self.backend.summary_inputs(scene, path, scope=scope)
        if not inputs["files"] and not inputs["directories"]:
            await self.backend.clear_summary(scene, path, scope=scope)
            return {"path": path, "cleared": True, "skipped": "目录为空，已清除派生摘要"}
        binding = self.config.models.roles.memory
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        partition = "公共分区" if scope == "public" else scene
        messages = [{"role": "system", "content": self._prompt},
                    {"role": "user", "content": encode({"partition": partition,
                                                        "directory": path or "（分区根目录）", **inputs})}]
        estimate = estimate_request(messages, [], binding.max_output_tokens)
        request = {"provider": binding.provider, "settings": self.model.settings.model_dump(exclude={"api_key"}),
                   "messages": messages, "estimated_total_tokens": estimate,
                   "context_window_tokens": binding.context_window_tokens,
                   "price": None if price is None else price.model_dump(mode="json")}
        source = "public" if scope == "public" else scene
        if estimate > binding.context_window_tokens:
            raise ContextBudgetError(f"memory summary request estimated {estimate} tokens, exceeding "
                                     f"configured window {binding.context_window_tokens}; model was not called")
        run = None
        try:
            async with (self.slots.slot(direct=False, scene=scene) if self.slots is not None else nullcontext()):
                run = self.jobs.begin_summary(scene, source, path, request)
                try:
                    reply = await self.model.complete(messages, [])
                except ModelProtocolError as error:
                    self.jobs.summary_response(run, error.response, error.usage, estimate_cost(price, error.token_usage))
                    raise
            self.jobs.summary_response(run, {"message": reply.message, "finish_reason": reply.finish_reason},
                                       reply.usage, estimate_cost(price, reply.token_usage))
            abstract, overview = parse_summary(reply)
            summary = await self.backend.write_summary(scene, path, abstract, overview, scope=scope)
        except asyncio.CancelledError as error:
            if run is not None:
                self.jobs.finish_summary(run, "interrupted", _error_text(error))
            raise
        except Exception as error:
            if run is not None:
                self.jobs.finish_summary(run, "failed", _error_text(error))
            raise
        self.jobs.finish_summary(run, "complete")
        return {"run_id": run, **asdict(summary)}

    async def refresh_after_writes(self, scene: str, paths: list[str]) -> dict:
        """Deepest first; the first failure stops ancestors that would read stale summaries."""
        done: list[dict] = []
        for directory in affected_directories(paths):
            try:
                done.append(await self.summarize(scene, directory))
            except (FileNotFoundError, NotADirectoryError) as error:
                done.append({"path": directory, "skipped": _error_text(error)})
            except Exception as error:
                return {"results": done, "failed": directory, "error": _error_text(error)}
        return {"results": done, "failed": None, "error": None}
