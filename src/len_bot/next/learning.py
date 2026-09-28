"""Bounded, scene-local extraction of expression candidates from actual group messages."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import HostConfig
from .context import ContextBudgetError, estimate_request
from .expression_selection import ExpressionService
from .learning_store import LearningStore
from .messages import ChatMessage, plain_text
from .model import ChatModel, ModelProtocolError, ModelReply
from .model_slots import ModelSlots
from .pricing import estimate_cost
from .store import Store, encode


LOG = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_expression_learning.md"
WAKE_INTERVAL_SECONDS = 30
MAX_CANDIDATES = 20


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _candidates(reply: ModelReply, allowed: set[int]) -> list[tuple[str, str, list[int]]]:
    """Parse the single external model response, preserving its fragment on failure."""
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("learner returned tool calls without any learner tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {"expressions"}:
            raise ValueError("expected an object containing only expressions")
        expressions = value["expressions"]
        if not isinstance(expressions, list) or len(expressions) > MAX_CANDIDATES:
            raise ValueError(f"expressions must be an array of at most {MAX_CANDIDATES}")
        candidates: list[tuple[str, str, list[int]]] = []
        for index, item in enumerate(expressions):
            if not isinstance(item, dict) or set(item) != {"situation", "style", "sources"}:
                raise ValueError(f"expressions[{index}] has unknown or missing fields")
            situation, style, sources = item["situation"], item["style"], item["sources"]
            if not isinstance(situation, str) or not situation.strip():
                raise ValueError(f"expressions[{index}].situation must be nonblank text")
            if not isinstance(style, str) or not style.strip():
                raise ValueError(f"expressions[{index}].style must be nonblank text")
            if not isinstance(sources, list) or not sources:
                raise ValueError(f"expressions[{index}].sources must be a nonempty array")
            used_sources: set[int] = set()
            for source in sources:
                if type(source) is not int or source not in allowed:
                    raise ValueError(f"expressions[{index}].sources contains a non-input record: {source!r}")
                if source in used_sources:
                    raise ValueError(f"duplicate expression source record: {source}")
                used_sources.add(source)
            candidates.append((situation, style, sources))
        return candidates
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid expression learning response: {error}; response fragment: {fragment!r}") from error


class ExpressionLearner:
    """One background worker per enabled group; failures require an explicit retry."""

    def __init__(self, config: HostConfig, store: Store, model: ChatModel, *,
                 slots: ModelSlots | None = None, on_update: Callable[[], None] | None = None,
                 expression_service: ExpressionService | None = None):
        self.config = config
        self.store = store
        self.model = model
        self.slots = slots
        self.expression_service = expression_service
        self.on_update = on_update
        self.records = LearningStore(store)
        self.scenes = tuple(scene for scene, settings in config.scenes.items()
                            if settings.learning is not None and settings.learning.extract)
        self._wake = {scene: asyncio.Event() for scene in self.scenes}
        self._force: set[str] = set()
        self._retry: set[str] = set()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.errors: dict[str, BaseException] = {}
        self._prompt = PROMPT.read_text(encoding="utf-8")
        for scene in self.scenes:
            self.records.initialize(scene, store.max_message_seq(scene))
            self.records.recover(scene)

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("expression learning workers already started")
        for scene in self.scenes:
            self._workers[scene] = asyncio.create_task(self._worker(scene), name=f"expression-learning:{scene}")

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
            raise ValueError(f"expression learning is not configured for scene {scene!r}")
        if scene in self.errors:
            raise RuntimeError(f"expression learning worker stopped for {scene}: "
                               f"{_error_text(self.errors[scene])}") from self.errors[scene]

    def request(self, scene: str) -> None:
        """Wake one current batch even below time/count thresholds, not a failed batch."""
        self._scene(scene)
        latest = self.records.latest(scene)
        if latest is not None and latest["status"] in {"failed", "interrupted"}:
            raise ValueError(f"expression learning range failed for {scene}; explicitly retry it")
        self._force.add(scene)
        self._wake[scene].set()

    def retry(self, scene: str) -> None:
        """Explicitly repeat the most recent failed/interrupted source range once."""
        self._scene(scene)
        latest = self.records.latest(scene)
        if latest is None or latest["status"] not in {"failed", "interrupted"}:
            raise ValueError(f"no failed or interrupted expression learning range for {scene}")
        self._retry.add(scene)
        self._wake[scene].set()

    def state(self, scene: str) -> dict:
        if scene not in self._wake:
            raise ValueError(f"expression learning is not configured for scene {scene!r}")
        cursor = self.records.state(scene)
        latest = self.records.latest(scene)
        error = self.errors.get(scene)
        worker = self._workers.get(scene)
        return {"scene": scene, "after_seq": cursor["after_seq"], "running": worker is not None and not worker.done(),
                "latest": latest,
                "worker_error": None if error is None else _error_text(error)}

    async def _worker(self, scene: str) -> None:
        try:
            while True:
                wake = self._wake[scene]
                wake.clear()
                if await self._step(scene):
                    await asyncio.sleep(0)
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=WAKE_INTERVAL_SECONDS)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors[scene] = error
            LOG.exception("expression learning worker stopped for %s", scene)
            if self.on_update is not None:
                self.on_update()

    async def _step(self, scene: str) -> bool:
        latest = self.records.latest(scene)
        retry = scene in self._retry
        if latest is not None and latest["status"] in {"failed", "interrupted", "running"}:
            if not retry:
                self._force.discard(scene)
                return False
            self._retry.discard(scene)
            through, rows = self._scan(scene, latest["after_seq"], through=latest["through_seq"], limit=100)
            if through != latest["through_seq"] or not rows:
                raise ValueError(f"expression learning source range {latest['id']} is no longer readable")
            await self._run(scene, latest["after_seq"], through, rows)
            return True
        after = self.records.state(scene)["after_seq"]
        through, rows = self._scan(scene, after)
        if through == after:
            self._force.discard(scene)
            return False
        if not rows:
            self.records.advance_empty(scene, after, through)
            if self.on_update is not None:
                self.on_update()
            return True
        settings = self.config.scenes[scene].learning
        force = scene in self._force
        last = self.records.last_input_at(scene, after, exclude_uids=self._exclude_uids(scene))
        if last is None:
            raise ValueError(f"expression learning input for {scene} has no actual arrival time")
        now = self.store.now()
        due = (force or now - rows[0][2] >= settings.max_age_seconds
               or (len(rows) >= settings.min_messages and now - last >= settings.idle_seconds))
        if not due:
            return False
        self._force.discard(scene)
        await self._run(scene, after, through, rows)
        return True

    def _exclude_uids(self, scene: str) -> tuple[str, ...]:
        return (self.config.bot_qq, *self.config.scenes[scene].attention.other_bot_qqs)

    def _scan(self, scene: str, after: int, *, through: int | None = None, limit: int | None = None
              ) -> tuple[int, list[tuple[int, ChatMessage, float]]]:
        settings = self.config.scenes[scene].learning
        return self.records.scan(scene, after, limit=settings.batch_size if limit is None else limit,
                                 exclude_uids=self._exclude_uids(scene), through=through)

    async def _run(self, scene: str, after: int, through: int,
                   rows: list[tuple[int, ChatMessage, float]]) -> None:
        settings = self.config.scenes[scene].learning
        binding = self.config.models.roles.learner
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        timezone = ZoneInfo(self.config.timezone)
        source = [{"record": seq, "qq": message.sender.uid,
                   "display_name": message.sender.card or message.sender.nickname,
                   "platform_message_id": message.platform_message_id,
                   "platform_time": datetime.fromtimestamp(message.time, timezone).isoformat(),
                   "received_at": datetime.fromtimestamp(received_at, timezone).isoformat(),
                   "text": plain_text(message)} for seq, message, received_at in rows]
        messages = [{"role": "system", "content": self._prompt},
                    {"role": "user", "content": encode({"scene": scene, "messages": source})}]
        estimate = estimate_request(messages, [], binding.max_output_tokens)
        request = {"provider": binding.provider,
                   "settings": self.model.settings.model_dump(exclude={"api_key"}),
                   "messages": messages, "tools": [], "estimated_total_tokens": estimate,
                   "context_window_tokens": binding.context_window_tokens,
                   "price": None if price is None else price.model_dump(mode="json")}
        batch_id = self.records.begin(scene, after, through, request)
        try:
            if self.on_update is not None:
                self.on_update()
            if estimate > binding.context_window_tokens:
                raise ContextBudgetError(
                    f"expression learning request estimated {estimate} tokens, exceeding "
                    f"configured window {binding.context_window_tokens}; model was not called")
            async with (self.slots.slot(direct=False) if self.slots is not None else nullcontext()):
                self.records.mark_model_started(batch_id)
                try:
                    reply = await self.model.complete(messages, [])
                except ModelProtocolError as error:
                    self.records.response(batch_id, error.response, error.usage,
                                          estimate_cost(price, error.token_usage))
                    raise
            self.records.response(batch_id,
                                  {"message": reply.message, "finish_reason": reply.finish_reason},
                                  reply.usage, estimate_cost(price, reply.token_usage))
            candidates = _candidates(reply, {seq for seq, _, _ in rows})
            if self.expression_service is None:
                self.records.complete(batch_id, candidates, auto_adopt=settings.auto_adopt)
            else:
                await self.expression_service.complete(scene, batch_id, candidates, auto_adopt=settings.auto_adopt)
        except asyncio.CancelledError as error:
            self.records.fail(batch_id, "interrupted", _error_text(error))
            if self.on_update is not None:
                self.on_update()
            raise
        except Exception as error:
            self.records.fail(batch_id, "failed", _error_text(error))
            LOG.exception("expression learning batch %s failed for %s", batch_id, scene)
        if self.on_update is not None:
            self.on_update()
