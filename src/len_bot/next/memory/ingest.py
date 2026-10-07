"""Background extraction of actual stored messages into local memory.

The separate processing database owns successful positions and real call/task
results.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import time
from typing import TYPE_CHECKING, Sequence

from .service import MemoryService
from .extract import extract_local
from .local import LocalMemoryChange
from ..models.client import ChatModel
from ..models.slots import ModelSlots
from ..platform.messages import ChatMessage
from ..storage.store import Store
from ..runtime.logs import log_context

if TYPE_CHECKING:
    from ..config import SharedConfig


LOG = logging.getLogger(__name__)
WAKE_INTERVAL_SECONDS = 30


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class MemoryIngestor:
    """One worker per configured scene; request/retry only signal work."""

    def __init__(self, config: SharedConfig, store: Store, memory: MemoryService,
                 scenes: Sequence[str], *, model: ChatModel,
                 slots: ModelSlots | None = None):
        settings = memory.settings.ingest
        if settings is None:
            raise ValueError("memory ingestion is not configured")
        self.config = config
        self.store = store
        self.memory = memory
        self.model = model
        self.slots = slots
        self.settings = settings
        self.scenes = tuple(dict.fromkeys(scenes))
        self.jobs = memory.jobs
        self._wake = {scene: asyncio.Event() for scene in self.scenes}
        self._force: set[str] = set()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.errors: dict[str, BaseException] = {}
        for scene in self.scenes:
            self.jobs.initialize(scene, store.max_message_seq(scene))
            latest = self.jobs.latest(scene)
            if latest is None:
                continue
            if latest["backend"] != memory.settings.backend and latest["status"] != "complete":
                raise ValueError(
                    f"scene {scene}: unfinished {latest['backend']} memory job "
                    f"{latest['id']} cannot be processed by {memory.settings.backend}")

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("memory workers already started")
        for scene in self.scenes:
            with log_context(scene=scene, job='memory_ingest'):
                self._workers[scene] = asyncio.create_task(self._worker(scene), name=f"memory-ingest:{scene}")

    async def close(self) -> None:
        for worker in self._workers.values():
            worker.cancel()
        if self._workers:
            results = await asyncio.gather(*self._workers.values(), return_exceptions=True)
            for scene, result in zip(self._workers, results, strict=True):
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    self.errors[scene] = result
        self._workers.clear()

    def _scene(self, scene: str) -> None:
        if scene not in self._wake:
            raise ValueError(f"memory ingestion scene is not configured: {scene!r}")
        if scene in self.errors:
            raise RuntimeError(f"memory ingestion worker stopped for {scene}: "
                               f"{_error_text(self.errors[scene])}") from self.errors[scene]

    def request(self, scene: str) -> None:
        """Wake this scene and permit one current batch below ordinary thresholds."""
        self._scene(scene)
        self._force.add(scene)
        self._wake[scene].set()

    def retry(self, scene: str) -> dict:
        """Explicitly reprocess the same failed local source range."""
        self._scene(scene)
        latest = self.jobs.latest(scene)
        if latest is None or latest["backend"] != "local" or latest["status"] not in {"failed", "interrupted"}:
            raise ValueError("no failed or interrupted local memory range to retry")
        job = self.jobs.create(scene, "local", latest["first_seq"], latest["through_seq"])
        job["details"]["retry_of"] = latest["id"]
        self.jobs.details(job)
        self._wake[scene].set()
        return job

    async def _worker(self, scene: str) -> None:
        try:
            while True:
                wake = self._wake[scene]
                wake.clear()
                if await self._step(scene):
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=WAKE_INTERVAL_SECONDS)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors[scene] = error
            LOG.exception("memory ingestion worker stopped for %s", scene)

    async def _step(self, scene: str) -> bool:
        latest = self.jobs.latest(scene)
        if latest is not None:
            if latest["backend"] != self.memory.settings.backend and latest["status"] != "complete":
                raise ValueError(f"scene {scene}: unfinished {latest['backend']} job blocks backend change")
            if latest["status"] == "queued":
                self._force.discard(scene)
                await self._run_local(scene, latest)
                return True
            if latest["status"] in {"failed", "interrupted", "running"}:
                self._force.discard(scene)
                return False
        after = self.jobs.after(scene)
        excluded = self.jobs.excluded_records(scene, after=after)
        rows = self.store.memory_messages(
            scene, after, limit=max(self.settings.batch_size, self.settings.min_messages),
            exclude_records=excluded,
        )
        force = scene in self._force
        self._force.discard(scene)
        if not rows:
            return False
        now = time.time()
        oldest = rows[0][2]
        last = self.store.last_memory_input_at(scene, after, exclude_records=excluded)
        if last is None:
            raise ValueError(f"scene {scene}: stored memory input has no actual arrival/sent time")
        due = (force or now - oldest >= self.settings.max_age_seconds
               or (len(rows) >= self.settings.min_messages
                   and now - last >= self.settings.idle_seconds))
        if not due:
            return False
        rows = rows[:self.settings.batch_size]
        job = self.jobs.create(scene, self.memory.settings.backend, rows[0][0], rows[-1][0])
        job["details"]["message_count"] = len(rows)
        self.jobs.details(job)
        await self._run_local(scene, job)
        return True

    def _selected_input(self, scene: str, job: dict) -> list[tuple[int, ChatMessage, float, str | None]]:
        """Read under the scene write lock, after any earlier forget has finished."""
        excluded = self.jobs.excluded_records(scene, after=job["first_seq"] - 1,
                                              through=job["through_seq"])
        rows = self.store.memory_messages(scene, job["first_seq"] - 1, limit=100,
                                          through=job["through_seq"], exclude_records=excluded)
        job["details"].update(excluded_records=excluded, selected_message_count=len(rows),
                              source_personas=[{'record': seq, 'message_id': message.id, 'persona_id': persona_id}
                                               for seq, message, _, persona_id in rows])
        self.jobs.details(job)
        if not rows:
            if not excluded:
                raise ValueError(f"memory source range has no readable original messages: {job['id']}")
            job["details"]["result"] = {"status": "input_excluded", "write_count": 0,
                                        "model_calls": 0}
            self.jobs.status(job, "complete")
        return rows

    async def _run_local(self, scene: str, job: dict) -> None:
        binding = self.config.models.roles.memory
        self.jobs.status(job, "running")

        def start_call(request: dict) -> int:
            calls = job["details"]["calls"]
            snapshot = copy.deepcopy(request)
            snapshot["provider"] = binding.provider
            calls.append({"started": time.time(), "request": snapshot})
            self.jobs.details(job)
            return len(calls) - 1

        def finish_call(index: int, response: object | None, usage: dict | None,
                        error: str | None, tokens: dict | None) -> None:
            job["details"]["calls"][index].update(
                ended=time.time(), response=copy.deepcopy(response),
                usage=copy.deepcopy(usage), error=error, tokens=copy.deepcopy(tokens))
            self.jobs.details(job)

        def record_tool(call_id: str, name: str, arguments: dict,
                        result: str, error: str | None) -> None:
            job["details"]["tools"].append({
                "call_id": call_id, "name": name, "arguments": copy.deepcopy(arguments),
                "result": result, "error": error,
            })
            self.jobs.details(job)

        def record_write(change: LocalMemoryChange) -> None:
            job["details"]["writes"].append({
                "path": change.path, "action": change.action,
                "changed_at": change.changed_at, "reason": change.reason,
                "before_chars": None if change.before is None else len(change.before),
                "after_chars": None if change.after is None else len(change.after),
            })
            self.jobs.details(job)

        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                async with self.memory.write_lock(scene):
                    rows = self._selected_input(scene, job)
                    if not rows:
                        return
                    result = await extract_local(
                        scene, [message for _, message, _, _ in rows], self.memory.backend,
                        self.model, persona_ids=[persona_id for _, _, _, persona_id in rows],
                        known_persona_ids=self.memory.known_persona_ids(scene),
                        timezone=self.config.scene_timezone(scene),
                        context_window_tokens=binding.context_window_tokens,
                        max_steps=self.settings.max_steps, start_call=start_call,
                        finish_call=finish_call, record_tool=record_tool,
                        record_write=record_write, slots=self.slots,
                    )
        except asyncio.CancelledError as error:
            detail = "Process stopped during local extraction"
            if error.__cause__ is not None:
                detail += "; write also failed: " + _error_text(error.__cause__)
            self.jobs.status(job, "interrupted", detail)
            raise
        except Exception as error:
            self.jobs.status(job, "failed", _error_text(error))
            return
        job["details"]["result"] = asdict(result)
        if result.failed_tools:
            self.jobs.status(job, "failed", f"{result.failed_tools} memory tool call(s) failed")
            return
        self.jobs.status(job, "complete")
        written = [item["path"] for item in job["details"]["writes"]]
        if self.memory.summarizer is not None and written:
            # A separate unit: summary failures are recorded without reopening the batch.
            async with self.memory.write_lock(scene):
                job["details"]["summaries"] = await self.memory.summarizer.refresh_after_writes(scene, written)
            self.jobs.details(job)


@asynccontextmanager
async def open_memory_ingestor(config: SharedConfig, store: Store,
                               memory: MemoryService | None, scenes: Sequence[str],
                               slots: ModelSlots | None = None):
    """Open only explicitly enabled extraction, closing workers before clients."""
    if memory is None or memory.settings.ingest is None:
        yield None
        return
    async with ChatModel(config.model_settings("memory")) as model:
        ingestor = MemoryIngestor(config, store, memory, scenes, model=model, slots=slots)
        ingestor.start()
        try:
            yield ingestor
        finally:
            await ingestor.close()
