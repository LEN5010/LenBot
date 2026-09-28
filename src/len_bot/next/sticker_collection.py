"""One concrete group-image collection worker and a strict vision response boundary."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from len_bot.media.images import image_block

from .config import HostConfig
from .context import ContextBudgetError, estimate_text_request
from .http_read import fetch_public
from .images import image_url, prepare_pixels
from .messages import plain_text
from .model import ChatModel, ModelProtocolError, ModelReply
from .model_slots import ModelSlots
from .pricing import estimate_cost
from .sticker_assets import MAX_STICKER_BYTES, inspect_sticker
from .sticker_store import StickerStore
from .store import Store, encode


LOG = logging.getLogger(__name__)
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_sticker_learning.md"
WAKE_INTERVAL_SECONDS = 30


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _annotation(reply: ModelReply) -> tuple[str, str, list[str], list[str], bool]:
    fragment = reply.text[:500]
    try:
        if reply.tool_calls:
            raise ValueError("sticker vision returned tool calls without tools")
        value = json.loads(reply.text, parse_constant=_reject_constant)
        if not isinstance(value, dict) or set(value) != {
            "description", "text", "emotions", "tags", "is_sticker"
        }:
            raise ValueError("expected exactly description, text, emotions, tags and is_sticker")
        description, text_value = value["description"], value["text"]
        emotions, tags, is_sticker = value["emotions"], value["tags"], value["is_sticker"]
        if not isinstance(description, str) or not description.strip():
            raise ValueError("description must be nonblank text")
        if not isinstance(text_value, str):
            raise ValueError("text must be a string, empty when no words are visible")
        for field, labels in (("emotions", emotions), ("tags", tags)):
            if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip()
                                                   for label in labels):
                raise ValueError(f"{field} must be an array of nonblank strings")
        if type(is_sticker) is not bool:
            raise ValueError("is_sticker must be a boolean")
        return description, text_value, emotions, tags, is_sticker
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid sticker vision response: {error}; response fragment: {fragment!r}") from error


class StickerCollector:
    """Per-scene queued candidates; failed work is never silently replayed."""

    def __init__(self, config: HostConfig, store: Store, vision: ChatModel, *,
                 slots: ModelSlots | None = None, on_update: Callable[[], None] | None = None):
        self.config = config
        self.store = store
        self.vision = vision
        self.slots = slots
        self.on_update = on_update
        self.records = StickerStore(store)
        self.scenes = tuple(scene for scene, settings in config.scenes.items()
                            if settings.learning is not None and settings.learning.collect_stickers)
        self._wake = {scene: asyncio.Event() for scene in self.scenes}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self.errors: dict[str, BaseException] = {}
        self._prompt = PROMPT.read_text(encoding="utf-8")
        for scene in self.scenes:
            self.records.recover(scene)

    def start(self) -> None:
        if self._workers:
            raise RuntimeError("sticker collection workers already started")
        for scene in self.scenes:
            self._workers[scene] = asyncio.create_task(self._worker(scene), name=f"sticker-collection:{scene}")

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
            raise ValueError(f"sticker collection is not configured for scene {scene!r}")
        if scene in self.errors:
            raise RuntimeError(f"sticker worker stopped for {scene}: {_error_text(self.errors[scene])}") \
                from self.errors[scene]

    def request(self, scene: str) -> None:
        """Wake the worker to inspect its saved queued candidates."""
        self._scene(scene)
        self._wake[scene].set()

    def retry(self, scene: str, id: int) -> dict:
        """Explicitly requeue one failed/interrupted candidate, reusing saved original."""
        self._scene(scene)
        candidate = self.records.retry(scene, id)
        self._wake[scene].set()
        if self.on_update is not None:
            self.on_update()
        return candidate

    def state(self, scene: str) -> dict:
        if scene not in self._wake:
            raise ValueError(f"sticker collection is not configured for scene {scene!r}")
        worker = self._workers.get(scene)
        error = self.errors.get(scene)
        return {"scene": scene, "running": worker is not None and not worker.done(),
                "worker_error": None if error is None else _error_text(error),
                "counts": self.records.counts(scene)}

    async def _worker(self, scene: str) -> None:
        try:
            while True:
                wake = self._wake[scene]
                wake.clear()
                candidate = self.records.next_queued(scene)
                if candidate is not None:
                    await self._process(scene, candidate)
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
            LOG.exception("sticker collection worker stopped for %s", scene)
            if self.on_update is not None:
                self.on_update()

    async def _process(self, scene: str, candidate: dict) -> None:
        id = candidate["id"]
        call_id = self.records.begin(scene, id)
        try:
            if self.on_update is not None:
                self.on_update()
            message = self.store.read_message(scene, candidate["source_message_seq"])
            if message is None:
                raise ValueError(f"sticker source message {candidate['source_message_seq']} is unavailable in {scene}")
            pictures = [segment for segment in message.segments if segment.type == "image"]
            image_index = candidate["image_index"]
            if image_index > len(pictures):
                raise ValueError(f"sticker source message {candidate['source_message_seq']} has only "
                                 f"{len(pictures)} images, not image {image_index}")
            original = self.records.original(scene, id)
            deadline = asyncio.timeout(self.config.images.timeout_seconds)
            try:
                async with deadline:
                    if original is None:
                        url = image_url(pictures[image_index - 1].data)
                        _, _, data = await fetch_public(
                            url, self.config.images.timeout_seconds,
                            lambda _type, _prefix: MAX_STICKER_BYTES,
                        )
                        mime_type, width, height, animated = await asyncio.to_thread(inspect_sticker, data)
                        self.records.save_original(scene, id, data=data, mime_type=mime_type,
                                                   width=width, height=height, animated=animated)
                    else:
                        _, data = original
                    jpeg, animated_first_frame_only, width, height = await prepare_pixels(data, self.config.images)
            except TimeoutError as error:
                if deadline.expired():
                    raise TimeoutError(f"sticker download and preparation exceeded "
                                       f"{self.config.images.timeout_seconds} seconds") from error
                raise

            timezone = ZoneInfo(self.config.scene_timezone(scene))
            source = {"scene": scene, "source_message_seq": candidate["source_message_seq"],
                      "image_index": image_index, "qq": message.sender.uid,
                      "display_name": message.sender.card or message.sender.nickname,
                      "platform_message_id": message.platform_message_id,
                      "platform_time": datetime.fromtimestamp(message.time, timezone).isoformat(),
                      "original_text": plain_text(message),
                      "prepared_width": width, "prepared_height": height,
                      "animated_first_frame_only": animated_first_frame_only}
            messages = [{"role": "system", "content": self._prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": encode(source)}, image_block(jpeg),
                        ]}]
            binding = self.config.models.roles.vision
            price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
            estimated = estimate_text_request(messages, [], binding.max_output_tokens)
            request = {"provider": binding.provider,
                       "settings": self.vision.settings.model_dump(exclude={"api_key"}),
                       "messages": messages, "tools": [],
                       "estimated_text_tokens": estimated, "estimated_total_tokens": None,
                       "context_window_tokens": binding.context_window_tokens,
                       "price": None if price is None else price.model_dump(mode="json")}
            self.records.set_request(call_id, request)
            if estimated > binding.context_window_tokens:
                raise ContextBudgetError(
                    f"sticker vision text with reserved output estimated {estimated} tokens, "
                    f"exceeding configured window {binding.context_window_tokens}; model was not called")
            async with (self.slots.slot(direct=False) if self.slots is not None else nullcontext()):
                self.records.mark_model_started(call_id)
                try:
                    reply = await self.vision.complete(messages, [])
                except ModelProtocolError as error:
                    self.records.response(call_id, error.response, error.usage,
                                          estimate_cost(price, error.token_usage))
                    raise
            self.records.response(call_id,
                                  {"message": reply.message, "finish_reason": reply.finish_reason},
                                  reply.usage, estimate_cost(price, reply.token_usage))
            description, text_value, emotions, tags, is_sticker = _annotation(reply)
            self.records.complete(call_id, description=description, text=text_value,
                                  emotions=emotions, tags=tags, is_sticker=is_sticker)
        except asyncio.CancelledError as error:
            self.records.fail(call_id, "interrupted", _error_text(error))
            if self.on_update is not None:
                self.on_update()
            raise
        except Exception as error:
            self.records.fail(call_id, "failed", _error_text(error))
            LOG.exception("sticker candidate %s failed in %s", id, scene)
        if self.on_update is not None:
            self.on_update()
