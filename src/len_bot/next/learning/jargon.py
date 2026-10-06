"""Independent group-jargon discovery and meaning inference from actual messages."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import HostConfig
from ..chat.recap import ContextBudgetError, estimate_request
from .jargon_store import JargonStore
from .store import LearningStore
from ..platform.messages import ChatMessage, plain_text
from ..models.client import ChatModel, ModelProtocolError, ModelReply
from ..models.slots import ModelSlots
from ..models.tokens import token_record
from ..storage.store import Store, encode


LOG = logging.getLogger(__name__)
PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
WAKE_INTERVAL_SECONDS = 30
MAX_TERMS = 20


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _discovery(reply: ModelReply, actual: dict[int, str]) -> list[tuple[str, int]]:
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("jargon discovery returned tool calls without tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {"terms"}:
            raise ValueError("expected an object containing only terms")
        terms = value["terms"]
        if not isinstance(terms, list) or len(terms) > MAX_TERMS:
            raise ValueError(f"terms must be an array of at most {MAX_TERMS}")
        proposals: list[tuple[str, int]] = []
        for index, item in enumerate(terms):
            if not isinstance(item, dict) or set(item) != {"term", "source_seq"}:
                raise ValueError(f"terms[{index}] has unknown or missing fields")
            term, seq = item["term"], item["source_seq"]
            if not isinstance(term, str) or not term or term.strip() != term:
                raise ValueError(f"terms[{index}].term must be nonblank without outer whitespace")
            if type(seq) is not int or seq not in actual:
                raise ValueError(f"terms[{index}].source_seq is not a current input record: {seq!r}")
            if term not in actual[seq]:
                raise ValueError(f"terms[{index}].term does not occur in record {seq}: {term!r}")
            proposals.append((term, seq))
        return proposals
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid jargon discovery response: {error}; response fragment: {fragment!r}") from error


def _meaning(reply: ModelReply) -> tuple[str, float]:
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("jargon meaning returned tool calls without tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {"meaning", "confidence"}:
            raise ValueError("expected an object containing only meaning and confidence")
        meaning, confidence = value["meaning"], value["confidence"]
        if not isinstance(meaning, str) or not meaning.strip():
            raise ValueError("meaning must be nonblank text")
        if type(confidence) not in {int, float} or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be a finite number from 0 to 1")
        return meaning, float(confidence)
    except (json.JSONDecodeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid jargon meaning response: {error}; response fragment: {fragment!r}") from error


class JargonLearner:
    """One bounded worker per enabled group; failed units require an explicit decision."""

    def __init__(self, config: HostConfig, store: Store, model: ChatModel, *,
                 slots: ModelSlots | None = None, on_update: Callable[[], None] | None = None):
        self.config = config
        self.store = store
        self.model = model
        self.slots = slots
        self.on_update = on_update
        self.records = JargonStore(store)
        self.messages = LearningStore(store)
        self.scenes = tuple(scene for scene, settings in config.scenes.items()
                            if settings.learning is not None and settings.learning.jargon_extract)
        self._wake = {scene: asyncio.Event() for scene in self.scenes}
        self._force: set[str] = set()
        self._retry: set[str] = set()
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.errors: dict[str, BaseException] = {}
        self._discovery_prompt = (PROMPTS / "next_jargon_discovery.md").read_text(encoding="utf-8")
        self._meaning_prompt = (PROMPTS / "next_jargon_meaning.md").read_text(encoding="utf-8")
        for scene in self.scenes:
            self.records.initialize(scene, store.max_message_seq(scene))
            self.records.recover(scene)

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("jargon workers already started")
        for scene in self.scenes:
            self._workers[scene] = asyncio.create_task(self._worker(scene), name=f"jargon-learning:{scene}")

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
            raise ValueError(f"jargon learning is not configured for scene {scene!r}")
        if scene in self.errors:
            raise RuntimeError(f"jargon worker stopped for {scene}: {_error_text(self.errors[scene])}") \
                from self.errors[scene]

    def _abandoned(self, scene: str, call: dict) -> bool:
        if call["purpose"] != "meaning":
            return False
        term = self.records.item(scene, call["term_id"])
        return term is None or term["status"] == "rejected"

    def request(self, scene: str) -> None:
        self._scene(scene)
        latest = self.records.latest(scene)
        if (latest is not None and latest["status"] in {"failed", "interrupted"}
                and not self._abandoned(scene, latest)):
            raise ValueError(f"jargon unit failed for {scene}; explicitly retry it")
        self._force.add(scene)
        self._wake[scene].set()

    def retry(self, scene: str) -> None:
        self._scene(scene)
        latest = self.records.latest(scene)
        if latest is None or latest["status"] not in {"failed", "interrupted"}:
            raise ValueError(f"no failed or interrupted jargon unit for {scene}")
        if self._abandoned(scene, latest):
            raise ValueError("the failed meaning term was deleted or rejected; no model call was made")
        self._retry.add(scene)
        self._wake[scene].set()

    def state(self, scene: str) -> dict:
        if scene not in self._wake:
            raise ValueError(f"jargon learning is not configured for scene {scene!r}")
        cursor = self.records.state(scene)
        worker = self._workers.get(scene)
        error = self.errors.get(scene)
        return {"scene": scene, "start_seq": cursor["start_seq"], "after_seq": cursor["after_seq"],
                "running": worker is not None and not worker.done(), "latest": self.records.latest(scene),
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
            LOG.exception("jargon worker stopped for %s", scene)
            if self.on_update is not None:
                self.on_update()

    def _exclude_uids(self, scene: str) -> tuple[str, ...]:
        return (self.config.bot_id, *self.config.scenes[scene].attention.other_bot_ids)

    def _scan(self, scene: str, after: int, *, through: int | None = None, limit: int | None = None
              ) -> tuple[int, list[tuple[int, ChatMessage, float]]]:
        settings = self.config.scenes[scene].learning
        return self.messages.scan(scene, after, limit=settings.batch_size if limit is None else limit,
                                  exclude_uids=self._exclude_uids(scene), through=through)

    async def _step(self, scene: str) -> bool:
        latest = self.records.latest(scene)
        if latest is not None and latest["status"] in {"failed", "interrupted", "running"}:
            if scene in self._retry:
                self._retry.discard(scene)
                if latest["purpose"] == "discovery":
                    through, rows = self._scan(scene, latest["after_seq"], through=latest["through_seq"], limit=100)
                    if through != latest["through_seq"] or not rows:
                        raise ValueError(f"jargon discovery source range {latest['id']} is no longer readable")
                    await self._run_discovery(scene, latest["after_seq"], through, rows)
                else:
                    if self._abandoned(scene, latest):
                        return True
                    old = self.records.call(scene, latest["id"])
                    await self._run_meaning(scene, self.records.item(scene, latest["term_id"]),
                                            old["request"]["messages"], count=latest["inference_count"])
                return True
            if not self._abandoned(scene, latest):
                self._force.discard(scene)
                return False
        due = self.records.pending(scene)
        if due:
            await self._run_meaning(scene, due[0], self._meaning_messages(scene, due[0]), count=due[0]["count"])
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
        last = self.messages.last_input_at(scene, after, exclude_uids=self._exclude_uids(scene))
        if last is None:
            raise ValueError(f"jargon input for {scene} has no actual arrival time")
        now = self.store.now()
        if not (scene in self._force or now - rows[0][2] >= settings.max_age_seconds
                or (len(rows) >= settings.min_messages and now - last >= settings.idle_seconds)):
            return False
        self._force.discard(scene)
        await self._run_discovery(scene, after, through, rows)
        return True

    def _row(self, row: tuple[int, ChatMessage, float], timezone: ZoneInfo) -> dict:
        seq, message, received_at = row
        return {"record": seq, "qq": message.sender.uid,
                "display_name": message.sender.card or message.sender.nickname,
                "platform_message_id": message.platform_message_id,
                "platform_time": datetime.fromtimestamp(message.time, timezone).isoformat(),
                "received_at": datetime.fromtimestamp(received_at, timezone).isoformat(),
                "text": plain_text(message)}

    def _meaning_messages(self, scene: str, term: dict) -> list[dict]:
        timezone = ZoneInfo(self.config.scene_timezone(scene))
        sample_records = term["sample_seqs"][-20:]
        nearby = {row[0]: self._row(row, timezone) for seq in sample_records
                  for row in self.records.context(scene, seq, self._exclude_uids(scene))}
        return [{"role": "system", "content": self._meaning_prompt},
                {"role": "user", "content": encode({"scene": scene, "term": term["term"],
                                                      "actual_count": term["count"],
                                                      "sample_records": sample_records,
                                                      "messages": [nearby[seq] for seq in sorted(nearby)]})}]

    def _request(self, messages: list[dict]) -> tuple[dict, int]:
        binding = self.config.models.roles.learner
        estimated = estimate_request(messages, [], binding.max_output_tokens)
        return ({"provider": binding.provider,
                 "settings": self.model.settings.model_dump(exclude={"api_key"}),
                 "messages": messages, "tools": [], "estimated_total_tokens": estimated,
                 "context_window_tokens": binding.context_window_tokens}, estimated)

    async def _model_call(self, scene: str, call_id: int, messages: list[dict], estimated: int) -> ModelReply:
        binding = self.config.models.roles.learner
        if estimated > binding.context_window_tokens:
            raise ContextBudgetError(
                f"jargon request estimated {estimated} tokens, exceeding configured window "
                f"{binding.context_window_tokens}; model was not called")
        async with (self.slots.slot(direct=False, scene=scene) if self.slots is not None else nullcontext()):
            self.records.mark_model_started(call_id)
            try:
                reply = await self.model.complete(messages, [])
            except ModelProtocolError as error:
                self.records.response(call_id, error.response, error.usage,
                                      token_record(error.token_usage))
                raise
        self.records.response(call_id, {"message": reply.message, "finish_reason": reply.finish_reason},
                              reply.usage, token_record(reply.token_usage))
        return reply

    async def _run_discovery(self, scene: str, after: int, through: int,
                             rows: list[tuple[int, ChatMessage, float]]) -> None:
        timezone = ZoneInfo(self.config.scene_timezone(scene))
        messages = [{"role": "system", "content": self._discovery_prompt},
                    {"role": "user", "content": encode({"scene": scene,
                                                         "messages": [self._row(row, timezone) for row in rows]})}]
        request, estimated = self._request(messages)
        call_id = self.records.begin_discovery(scene, after, through, request)
        try:
            if self.on_update is not None:
                self.on_update()
            reply = await self._model_call(scene, call_id, messages, estimated)
            proposals = _discovery(reply, {seq: plain_text(message) for seq, message, _ in rows})
            self.records.complete_discovery(call_id, proposals, exclude_uids=self._exclude_uids(scene))
        except asyncio.CancelledError as error:
            self.records.fail(call_id, "interrupted", _error_text(error))
            if self.on_update is not None:
                self.on_update()
            raise
        except Exception as error:
            self.records.fail(call_id, "failed", _error_text(error))
            LOG.exception("jargon discovery %s failed for %s", call_id, scene)
        if self.on_update is not None:
            self.on_update()

    async def _run_meaning(self, scene: str, term: dict, messages: list[dict], *, count: int) -> None:
        request, estimated = self._request(messages)
        call_id = self.records.begin_meaning(scene, term["id"], count, request)
        try:
            if self.on_update is not None:
                self.on_update()
            reply = await self._model_call(scene, call_id, messages, estimated)
            meaning, confidence = _meaning(reply)
            self.records.complete_meaning(call_id, meaning, confidence)
        except asyncio.CancelledError as error:
            self.records.fail(call_id, "interrupted", _error_text(error))
            if self.on_update is not None:
                self.on_update()
            raise
        except Exception as error:
            self.records.fail(call_id, "failed", _error_text(error))
            LOG.exception("jargon meaning %s failed for %s", call_id, scene)
        if self.on_update is not None:
            self.on_update()
