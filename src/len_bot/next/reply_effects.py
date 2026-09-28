"""Close follow-up windows after confirmed expressions and judge them in explicit batches."""

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
from .messages import ChatMessage, render_message
from .model import ChatModel, ModelProtocolError, ModelReply
from .model_slots import ModelSlots
from .pricing import estimate_cost
from .reply_effect_store import REACTIONS, ReplyEffectStore
from .store import Store, encode


LOG = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_reply_effects.md"
WAKE_INTERVAL_SECONDS = 30
BATCH_LIMIT = 10


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def parse_results(reply: ModelReply, entries: set[int]) -> dict[int, tuple[str, str]]:
    """Parse the single judge response; every requested entry must appear exactly once."""
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("reply effect judge returned tool calls without tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {"results"}:
            raise ValueError("expected an object containing only results")
        items = value["results"]
        if not isinstance(items, list):
            raise ValueError("results must be an array")
        results: dict[int, tuple[str, str]] = {}
        for index, item in enumerate(items):
            if not isinstance(item, dict) or set(item) != {"entry_seq", "reaction", "reason"}:
                raise ValueError(f"results[{index}] has unknown or missing fields")
            entry, reaction, reason = item["entry_seq"], item["reaction"], item["reason"]
            if type(entry) is not int or entry not in entries:
                raise ValueError(f"results[{index}].entry_seq is not in this batch: {entry!r}")
            if entry in results:
                raise ValueError(f"duplicate result for entry_seq {entry}")
            if reaction not in REACTIONS:
                raise ValueError(f"results[{index}].reaction is not one of {list(REACTIONS)}: {reaction!r}")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"results[{index}].reason must be nonblank text")
            results[entry] = (reaction, reason)
        missing = sorted(entries - set(results))
        if missing:
            raise ValueError(f"missing results for entry_seq {missing}")
        return results
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid reply effect response: {error}; response fragment: {fragment!r}") from error


class ReplyEffectTracker:
    """One background worker per enabled group; failed batches wait for an explicit retry."""

    def __init__(self, config: HostConfig, store: Store, model: ChatModel, *,
                 slots: ModelSlots | None = None, on_update: Callable[[], None] | None = None):
        self.config = config
        self.store = store
        self.model = model
        self.slots = slots
        self.on_update = on_update
        # Set by the network runtime: host time the current OneBot connection began.
        self.connected_since: Callable[[], float | None] = lambda: None
        self.records = ReplyEffectStore(store)
        self.scenes = tuple(scene for scene, settings in config.scenes.items()
                            if settings.learning is not None and settings.learning.reply_effects)
        self._wake = {scene: asyncio.Event() for scene in self.scenes}
        self._force: set[str] = set()
        self._retry: dict[str, int] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.errors: dict[str, BaseException] = {}
        self._prompt = PROMPT.read_text(encoding="utf-8")
        for scene in self.scenes:
            self.records.recover(scene)

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("reply effect workers already started")
        for scene in self.scenes:
            self._workers[scene] = asyncio.create_task(self._worker(scene), name=f"reply-effects:{scene}")

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
            raise ValueError(f"reply effects are not configured for scene {scene!r}")
        if scene in self.errors:
            raise RuntimeError(f"reply effect worker stopped for {scene}: "
                               f"{_error_text(self.errors[scene])}") from self.errors[scene]

    def wake(self, scene: str) -> None:
        """A new arrival may complete an open window; errors stay with the worker."""
        if scene in self._wake and scene not in self.errors:
            self._wake[scene].set()

    def request(self, scene: str) -> None:
        self._scene(scene)
        if not self.records.waiting(scene):
            raise ValueError(f"{scene} 没有已关闭且待判断的回复效果样本")
        self._force.add(scene)
        self._wake[scene].set()

    def retry(self, scene: str, call_id: int) -> None:
        self._scene(scene)
        self.records.retryable(scene, call_id)
        if scene in self._retry:
            raise ValueError(f"{scene} 已有一个待执行的重做请求：{self._retry[scene]}")
        self._retry[scene] = call_id
        self._wake[scene].set()

    def state(self, scene: str) -> dict:
        if scene not in self._wake:
            raise ValueError(f"reply effects are not configured for scene {scene!r}")
        error = self.errors.get(scene)
        worker = self._workers.get(scene)
        return {"scene": scene, "running": worker is not None and not worker.done(),
                "open": len(self.records.open_effects(scene)), "waiting": len(self.records.waiting(scene)),
                "latest": self.records.latest_call(scene),
                "requested_retry": self._retry.get(scene),
                "worker_error": None if error is None else _error_text(error)}

    def _exclude_uids(self, scene: str) -> tuple[str, ...]:
        return (self.config.bot_qq, *self.config.scenes[scene].attention.other_bot_qqs)

    async def _worker(self, scene: str) -> None:
        try:
            while True:
                wake = self._wake[scene]
                wake.clear()
                delay = self._close_windows(scene)
                if await self._judge(scene):
                    await asyncio.sleep(0)
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors[scene] = error
            LOG.exception("reply effect worker stopped for %s", scene)
            if self.on_update is not None:
                self.on_update()

    def _close_windows(self, scene: str) -> float:
        """Freeze complete windows; return the wait until the next known deadline."""
        now = self.store.now()
        delay = float(WAKE_INTERVAL_SECONDS)
        closed = False
        for effect in self.records.open_effects(scene):
            observed = self.records.followups(scene, effect["first_sent_at"], effect["deadline"],
                                              exclude_uids=self._exclude_uids(scene))
            if len(observed) >= 5 or now >= effect["deadline"]:
                since = self.connected_since()
                self.records.close(effect["id"], observed,
                                   input_gap=since is None or since > effect["first_sent_at"])
                closed = True
            else:
                delay = min(delay, max(0.0, effect["deadline"] - now))
        if closed and self.on_update is not None:
            self.on_update()
        return delay

    async def _judge(self, scene: str) -> bool:
        retry_of = self._retry.pop(scene, None)
        if retry_of is not None:
            ids = self.records.retryable(scene, retry_of)
            await self._run(scene, ids, retry_of=retry_of)
            return True
        waiting = self.records.waiting(scene)
        if not waiting:
            self._force.discard(scene)
            return False
        settings = self.config.scenes[scene].learning
        due = (scene in self._force or len(waiting) >= BATCH_LIMIT
               or self.store.now() - waiting[0]["closed_at"] >= settings.max_age_seconds)
        if not due:
            return False
        self._force.discard(scene)
        await self._run(scene, [item["id"] for item in waiting[:BATCH_LIMIT]])
        return True

    def _line(self, scene: str, seq: int, message: ChatMessage, received_at: float | None) -> dict:
        timezone = self.config.scene_timezone(scene)
        quote = None if message.reply_to is None else self.store.find_message(scene, message.reply_to)
        return {"record": seq, "qq": message.sender.uid,
                "display_name": message.sender.card or message.sender.nickname,
                "is_bot": message.is_self,
                "received_at": None if received_at is None else
                datetime.fromtimestamp(received_at, ZoneInfo(timezone)).isoformat(),
                "text": render_message(message, timezone=timezone, reply=quote)}

    def _sample(self, scene: str, effect: dict) -> dict:
        sent, followups = [], []
        arrivals = self.records.arrivals(scene, effect["observed_seqs"])
        for seq in effect["message_seqs"]:
            message = self.store.read_message(scene, seq)
            if message is None:
                raise ValueError(f"Reply effect {effect['id']} expression message {seq} is missing in {scene}")
            sent.append(self._line(scene, seq, message, None))
        for seq in effect["observed_seqs"]:
            message = self.store.read_message(scene, seq)
            if message is None:
                raise ValueError(f"Reply effect {effect['id']} follow-up message {seq} is missing in {scene}")
            followups.append(self._line(scene, seq, message, arrivals[seq]))
        before = [self._line(scene, seq, message, None)
                  for seq, message in self.records.before(scene, min(effect["message_seqs"]))]
        return {"entry_seq": effect["entry_seq"], "partial": effect["partial"],
                "bot_expression": sent, "before": before, "followups": followups}

    async def _run(self, scene: str, ids: list[int], *, retry_of: int | None = None) -> None:
        binding = self.config.models.roles.learner
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        effects = self.records.effects(scene, ids)
        messages = [{"role": "system", "content": self._prompt},
                    {"role": "user", "content": encode({"scene": scene, "bot_qq": self.config.bot_qq,
                                                        "samples": [self._sample(scene, item) for item in effects]})}]
        estimate = estimate_request(messages, [], binding.max_output_tokens)
        request = {"provider": binding.provider,
                   "settings": self.model.settings.model_dump(exclude={"api_key"}),
                   "messages": messages, "tools": [], "estimated_total_tokens": estimate,
                   "context_window_tokens": binding.context_window_tokens,
                   "price": None if price is None else price.model_dump(mode="json"),
                   **({} if retry_of is None else {"retry_of": retry_of})}
        call_id = self.records.begin_call(scene, ids, request, retry_of=retry_of)
        try:
            if self.on_update is not None:
                self.on_update()
            if estimate > binding.context_window_tokens:
                raise ContextBudgetError(
                    f"reply effect request estimated {estimate} tokens, exceeding "
                    f"configured window {binding.context_window_tokens}; model was not called")
            async with (self.slots.slot(direct=False) if self.slots is not None else nullcontext()):
                self.records.mark_model_started(call_id)
                try:
                    reply = await self.model.complete(messages, [])
                except ModelProtocolError as error:
                    self.records.response(call_id, error.response, error.usage,
                                          estimate_cost(price, error.token_usage))
                    raise
            self.records.response(call_id, {"message": reply.message, "finish_reason": reply.finish_reason},
                                  reply.usage, estimate_cost(price, reply.token_usage))
            self.records.complete(call_id, parse_results(reply, {item["entry_seq"] for item in effects}))
        except asyncio.CancelledError as error:
            self.records.fail(call_id, "interrupted", _error_text(error))
            if self.on_update is not None:
                self.on_update()
            raise
        except Exception as error:
            self.records.fail(call_id, "failed", _error_text(error))
            LOG.exception("reply effect judgment %s failed for %s", call_id, scene)
        if self.on_update is not None:
            self.on_update()
