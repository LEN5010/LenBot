"""Background extraction of actual stored messages into the selected memory backend.

The separate processing database owns successful positions and real call/task
receipts. A service commit receipt is not a completed extraction.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
import time
from typing import TYPE_CHECKING, Sequence

from .memory import MemoryService
from .memory_extract import extract_local
from .memory_local import LocalMemory, LocalMemoryChange
from .memory_openviking import OpenVikingMemory
from .model import ChatModel
from .model_slots import ModelSlots
from .messages import ChatMessage
from .store import Store

if TYPE_CHECKING:
    from .config import SharedConfig


LOG = logging.getLogger(__name__)
WAKE_INTERVAL_SECONDS = 30


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


class MemoryIngestor:
    """One worker per configured scene; request/retry/refresh only signal work."""

    def __init__(self, config: SharedConfig, store: Store, memory: MemoryService,
                 scenes: Sequence[str], *, model: ChatModel | None,
                 slots: ModelSlots | None = None):
        settings = memory.settings.ingest
        if settings is None:
            raise ValueError("memory ingestion is not configured")
        if isinstance(memory.backend, LocalMemory) and model is None:
            raise ValueError("local memory ingestion requires models.roles.memory")
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
        self._refresh: set[str] = set()
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
            if latest["backend"] == "openviking":
                task_id = latest["details"].get("task_id")
                if latest["status"] == "submitted" and task_id:
                    memory.pending_native_tasks[scene] = task_id
                elif (latest["status"] in {"failed", "interrupted"}
                      and latest["details"].get("native_phase") == "submitting"
                      and not task_id):
                    # The commit may have happened, even without a receipt here.
                    memory.pending_native_tasks[scene] = "submission outcome unknown"

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("memory workers already started")
        for scene in self.scenes:
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
        """Explicitly reprocess the same failed local source range, never OV."""
        self._scene(scene)
        if not isinstance(self.memory.backend, LocalMemory):
            raise ValueError("OpenViking ingestion must refresh its original task, not rearchive messages")
        latest = self.jobs.latest(scene)
        if latest is None or latest["backend"] != "local" or latest["status"] not in {"failed", "interrupted"}:
            raise ValueError("no failed or interrupted local memory range to retry")
        job = self.jobs.create(scene, "local", latest["first_seq"], latest["through_seq"])
        job["details"]["retry_of"] = latest["id"]
        self.jobs.details(job)
        self._wake[scene].set()
        return job

    def refresh(self, scene: str) -> None:
        """Re-read an existing OV task; never create another service session."""
        self._scene(scene)
        if not isinstance(self.memory.backend, OpenVikingMemory):
            raise ValueError("only OpenViking has native extraction tasks")
        latest = self.jobs.latest(scene)
        if (latest is None or latest["backend"] != "openviking"
                or latest["status"] not in {"submitted", "failed"}
                or not latest["details"].get("task_id")):
            raise ValueError("no OpenViking task reference is available to refresh")
        self._refresh.add(scene)
        self.memory.pending_native_tasks[scene] = latest["details"]["task_id"]
        self._wake[scene].set()

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
                await self._run(scene, latest)
                return True
            if latest["status"] == "submitted":
                self._force.discard(scene)
                refresh = scene in self._refresh
                self._refresh.discard(scene)
                if latest["error"] is None or refresh:
                    await self._poll_native(scene, latest)
                    return False
                return False
            if (latest["status"] == "failed" and scene in self._refresh
                    and latest["details"].get("task_id")):
                self._force.discard(scene)
                self._refresh.discard(scene)
                await self._poll_native(scene, latest)
                return False
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
        await self._run(scene, job)
        return True

    async def _run(self, scene: str, job: dict) -> None:
        if isinstance(self.memory.backend, LocalMemory):
            await self._run_local(scene, job)
        else:
            await self._submit_native(scene, job)

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
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        self.jobs.status(job, "running")

        def start_call(request: dict) -> int:
            calls = job["details"]["calls"]
            snapshot = copy.deepcopy(request)
            snapshot["provider"] = binding.provider
            calls.append({"started": time.time(), "request": snapshot})
            self.jobs.details(job)
            return len(calls) - 1

        def finish_call(index: int, response: object | None, usage: dict | None,
                        error: str | None, cost: dict | None) -> None:
            job["details"]["calls"][index].update(
                ended=time.time(), response=copy.deepcopy(response),
                usage=copy.deepcopy(usage), error=error, cost=copy.deepcopy(cost))
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
                        record_write=record_write, price=price, slots=self.slots,
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

    async def _submit_native(self, scene: str, job: dict) -> None:
        try:
            async with self.memory.write_lock(scene):
                rows = self._selected_input(scene, job)
                if not rows:
                    return
                job["details"]["native_phase"] = "submitting"
                self.jobs.status(job, "running")
                self.memory.pending_native_tasks[scene] = "submitting"
                async with asyncio.timeout(self.settings.timeout_seconds):
                    receipt = await self.memory.backend.ingest(
                        scene, [message for _, message, _, _ in rows],
                        persona_ids=[persona_id for _, _, _, persona_id in rows])
        except asyncio.CancelledError:
            self.jobs.status(job, "failed", "Process stopped while OpenViking submission outcome was unknown")
            raise
        except Exception as error:
            self.jobs.status(job, "failed", _error_text(error))
            return
        job["details"]["receipt"] = asdict(receipt)
        if receipt.status == "skipped":
            job["details"]["native_phase"] = "skipped"
            self.jobs.status(job, "complete")
            self.memory.pending_native_tasks.pop(scene, None)
            return
        job["details"]["task_id"] = receipt.task_id
        job["details"]["native_phase"] = "submitted"
        self.jobs.status(job, "submitted")
        self.memory.pending_native_tasks[scene] = receipt.task_id

    async def _poll_native(self, scene: str, job: dict) -> None:
        task_id = job["details"].get("task_id")
        if not task_id:
            raise ValueError(f"OpenViking submitted job {job['id']} has no task reference")
        self.memory.pending_native_tasks[scene] = task_id
        try:
            async with self.memory.write_lock(scene):
                task = await self.memory.backend.ingest_status(scene, task_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Keep the accepted receipt and require an explicit refresh after
            # this failed observation; do not resubmit or call it complete.
            self.jobs.status(job, "submitted", _error_text(error))
            return
        job["details"]["native_task"] = asdict(task)
        if task.status == "completed":
            job["details"]["native_phase"] = "completed"
            self.jobs.status(job, "complete")
            self._refresh.discard(scene)
            self.memory.pending_native_tasks.pop(scene, None)
            if self.memory.settings.summaries and task.memories_extracted_total != 0:
                await self._refresh_native_overview(scene, job)
        elif task.status in {"failed", "cancelled"}:
            job["details"]["native_phase"] = task.status
            self.jobs.status(job, "failed", task.error or f"OpenViking task {task.status}")
            self._refresh.discard(scene)
            self.memory.pending_native_tasks.pop(scene, None)
        else:
            self.jobs.status(job, "submitted")

    async def _refresh_native_overview(self, scene: str, job: dict) -> None:
        """A derived-content operation; its failure never reopens successful extraction."""
        refresh = {"mode": "semantic_and_vectors", "recursive": True,
                   "wait": True, "started": self.store.now(), "directories": [], "requests": [],
                   "complete": False}
        job["details"]["overview_refresh"] = refresh
        self.jobs.details(job)
        try:
            async with self.memory.write_lock(scene):
                directories = await self.memory.backend.memory_directories(scene)
                refresh["directories"] = list(directories)
                self.jobs.details(job)
                for path in directories:
                    request = {"path": path, "started": self.store.now()}
                    refresh["requests"].append(request)
                    self.jobs.details(job)
                    try:
                        result = await self.memory.backend.refresh_overview(scene, path)
                        request["result"] = result.model_dump()
                        request["complete"] = result.failed_records == 0 and result.unsupported_records == 0
                    except asyncio.CancelledError:
                        request["error"] = "Process stopped while native overview outcome was unknown"
                        raise
                    except Exception as error:
                        request["error"] = _error_text(error)
                        raise
                    finally:
                        request["ended"] = self.store.now()
                        self.jobs.details(job)
                    if not request["complete"]:
                        refresh["complete"] = False
                        break
                else:
                    refresh["complete"] = True
        except asyncio.CancelledError:
            refresh["error"] = "Process stopped while native overview outcome was unknown"
            raise
        except Exception as error:
            refresh["error"] = _error_text(error)
            LOG.exception("Native memory overview refresh failed for %s", scene)
        finally:
            refresh["ended"] = self.store.now()
            self.jobs.details(job)


@asynccontextmanager
async def open_memory_ingestor(config: SharedConfig, store: Store,
                               memory: MemoryService | None, scenes: Sequence[str],
                               slots: ModelSlots | None = None):
    """Open only explicitly enabled extraction, closing workers before clients."""
    if memory is None or memory.settings.ingest is None:
        yield None
        return
    if isinstance(memory.backend, LocalMemory):
        async with ChatModel(config.model_settings("memory")) as model:
            ingestor = MemoryIngestor(config, store, memory, scenes, model=model, slots=slots)
            ingestor.start()
            try:
                yield ingestor
            finally:
                await ingestor.close()
    else:
        ingestor = MemoryIngestor(config, store, memory, scenes, model=None, slots=slots)
        ingestor.start()
        try:
            yield ingestor
        finally:
            await ingestor.close()
