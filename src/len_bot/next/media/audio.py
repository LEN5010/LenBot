"""Resolve one saved voice-message position and reuse its actual WAV and successful transcript."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models.asr import ASRProtocolError, transcribe_audio, transcription_tokens
from .audio_store import AudioStore
from ..models.slots import ModelSlots
from ..platform.platform_tools import PlatformCall
from ..platform.onebot_audio import fetch_record
from ..storage.store import Store, encode
from ..runtime.logs import log_context

if TYPE_CHECKING:
    from ..config import LabConfig

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "next_transcribe.md"
LOG = logging.getLogger(__name__)


class TranscribeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1)
    audio: int = Field(default=1, gt=0)
    refresh: bool = False

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be a nonblank platform message ID")
        return value


TRANSCRIBE_TOOL = {"type": "function", "function": {
    "name": "transcribe", "description": "把本场景已保存消息中的语音转成文字。message是实际平台消息ID，"
    "audio从1开始按该消息record段顺序计数；refresh仅重新识别已保存音频，不重新下载。",
    "parameters": TranscribeArguments.model_json_schema(),
}}




async def execute_transcribe(store: Store, config: LabConfig, arguments: TranscribeArguments, *,
                             turn_id: str | None, platform: PlatformCall, slots: ModelSlots | None,
                             direct: bool, notify) -> str:
    scene = config.scene
    message = store.find_message(scene, arguments.message)
    if message is None:
        raise ValueError(f"当前场景没有平台消息 {arguments.message}")
    records = [segment for segment in message.segments if segment.type == "audio"]
    if arguments.audio > len(records):
        raise ValueError(f"消息 {arguments.message} 只有 {len(records)} 段语音，没有第 {arguments.audio} 段")
    position = (scene, arguments.message, arguments.audio)
    row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                           position).fetchone()
    if row is not None and row["transcript"] is not None and not arguments.refresh:
        return display(row, arguments, audio_reused=row["wav"] is not None, reused=True)
    if arguments.refresh and (row is None or row["wav"] is None):
        raise ValueError("该语音尚未保存音频；先用refresh=false获取，不能只重新识别")
    if row is not None and row["wav"] is not None and row["status"] in {"failed", "interrupted"} and not arguments.refresh:
        raise ValueError(f"上次转写未完成：{row['error']}；用refresh=true明确重做")
    with store.db:
        store.db.execute("INSERT INTO audio_cache(scene,platform_id,audio_index,status,created,updated) "
            "VALUES (?,?,?,'running',?,?) ON CONFLICT(scene,platform_id,audio_index) "
            "DO UPDATE SET status='running',updated=excluded.updated,error=NULL", (*position, store.now(), store.now()))
    notify()
    return await process_audio(store, config, arguments, turn_id=turn_id, platform=platform,
                               slots=slots, direct=direct, notify=notify, row=row, records=records)


async def process_audio(store, config, arguments, *, turn_id, platform, slots, direct, notify, row, records):
    scene = config.scene
    position = (scene, arguments.message, arguments.audio)
    audio_reused = row is not None and row["wav"] is not None
    if not audio_reused:
        wav, duration = await fetch_record(platform, records[arguments.audio - 1].data, config.audio)
        with store.db:
            store.db.execute("UPDATE audio_cache SET wav=?,duration=?,fetched_at=? "
                             "WHERE scene=? AND platform_id=? AND audio_index=?", (wav, duration, store.now(), *position))
        row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                               position).fetchone()
    reused = row["transcript"] is not None and not arguments.refresh
    if not reused:
        binding = config.models.roles.asr
        provider = config.models.providers[binding.provider]
        async with (slots.slot(direct=direct, scene=scene) if slots is not None else nullcontext()):
            request = {
                "settings": binding.model_dump(mode="json"), "base_url": provider.base_url,
                "file": {"scene": scene, "platform_message_id": arguments.message, "audio": arguments.audio,
                         "content_type": "audio/wav", "bytes": len(row["wav"]), "duration": row["duration"],
                         "source": "audio_cache"}, "response_format": "json",
            }
            calls = AudioStore(store)
            call_id = (store.start_call(turn_id, "asr", request) if turn_id is not None
                       else calls.start_call(*position, request))
            def end_call(response, usage, error=None, *, tokens=None):
                if turn_id is None:
                    calls.end_call(call_id, response, usage, error, tokens=tokens)
                else:
                    store.end_call(call_id, response, usage, error, tokens=tokens)
            notify()
            try:
                reply = await transcribe_audio(binding, base_url=provider.base_url, api_key=provider.api_key, wav=row["wav"], proxy=provider.proxy)
            except BaseException as error:
                error_text = f"{type(error).__name__}: {error}"
                if isinstance(error, ASRProtocolError):
                    end_call(error.response, error.usage, error_text,
                             tokens=transcription_tokens(error.metering))
                else:
                    end_call(None, None, error_text)
                notify()
                raise
            end_call(reply.response, reply.usage, tokens=transcription_tokens(reply.metering))
            with store.db:
                store.db.execute("UPDATE audio_cache SET transcript=?,provider=?,model=?,transcribed_at=?,"
                    "status='complete',updated=?,error=NULL,announced_at=? "
                    "WHERE scene=? AND platform_id=? AND audio_index=?",
                    (reply.text, binding.provider, binding.model, store.now(), store.now(),
                     store.now() if turn_id is not None else None, *position))
            notify()
        row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                               position).fetchone()
    return display(row, arguments, audio_reused=audio_reused, reused=reused)


def display(row, arguments, *, audio_reused: bool, reused: bool) -> str:
    result = {key: row[key] for key in ("duration", "fetched_at", "transcript", "provider", "model", "transcribed_at")}
    result.update(scene=row["scene"], platform_message_id=arguments.message, audio=arguments.audio,
                  wav_bytes=len(row["wav"]), audio_reused=audio_reused, transcript_reused=reused)
    return encode(result) + "\n" + PROMPT.read_text(encoding="utf-8").strip()


class AudioService:
    def __init__(self, store: Store, configs: dict[str, LabConfig], platform: PlatformCall | None,
                 slots: ModelSlots | None, on_update):
        self.store, self.configs, self.platform, self.slots = store, configs, platform, slots
        self.records = AudioStore(store)
        self.on_update = on_update
        self.changed = {scene: asyncio.Event() for scene, cfg in configs.items() if cfg.transcribe_audio}
        self.workers: list[asyncio.Task] = []
        self.errors: dict[str, str] = {}
        self.locks = WeakValueDictionary()
        self.active: set[asyncio.Task] = set()
        self.closing = False
        for scene, config in configs.items():
            self.records.recover(scene, config.transcribe_audio)

    def request(self, scene: str) -> None:
        if scene in self.changed:
            self.changed[scene].set()

    def wait_remaining(self, scene: str, now: float) -> float:
        if scene not in self.changed or scene in self.errors or self.closing:
            return 0
        since = self.records.waiting_since(scene)
        return 0 if since is None else max(0, since + self.configs[scene].audio.wait_seconds - now)

    def start(self) -> None:
        self.workers = []
        for scene in self.changed:
            with log_context(scene=scene, job='audio'):
                self.workers.append(asyncio.create_task(self.run(scene), name=f"audio:{scene}"))

    async def close(self) -> None:
        self.closing = True
        tasks = set(self.workers) | self.active
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.workers = []

    async def run(self, scene: str) -> None:
        try:
            while True:
                self.changed[scene].clear()
                item = self.records.pending(scene)
                if item is None:
                    await self.changed[scene].wait()
                    continue
                try:
                    await self.transcribe(scene, TranscribeArguments(message=item[0], audio=item[1]), automatic=True)
                except sqlite3.Error:
                    raise
                except Exception as error:
                    LOG.error("Audio %s %s/%s: %s: %s", scene, item[0], item[1], type(error).__name__, error)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.errors[scene] = f"{type(error).__name__}: {error}"
            LOG.exception("Audio worker stopped for %s", scene)
            self.on_update(scene)

    async def transcribe(self, scene: str, arguments: TranscribeArguments, *,
                         turn_id: str | None = None, direct: bool = False, automatic: bool = False) -> str | None:
        if self.closing:
            raise RuntimeError("Audio processing is stopping")
        task = asyncio.create_task(self.process(scene, arguments, turn_id=turn_id, direct=direct, automatic=automatic))
        self.active.add(task)
        try:
            return await task
        except asyncio.CancelledError as error:
            if self.closing and not asyncio.current_task().cancelling():
                raise RuntimeError("Audio processing stopped; unfinished recognition was cancelled, not replayed") from error
            raise
        finally:
            self.active.discard(task)

    async def process(self, scene: str, arguments: TranscribeArguments, *,
                      turn_id: str | None, direct: bool, automatic: bool) -> str | None:
        config = self.configs[scene]
        if config.models.roles.asr is None or self.platform is None:
            raise ValueError("Audio processing requires configured ASR and a real OneBot transport")
        position = (scene, arguments.message, arguments.audio)
        lock = self.locks.get(position)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[position] = lock
        async with lock:
            if automatic:
                row = self.records.item(*position)
                if row is None or row["status"] != "queued":
                    return None
            try:
                return await execute_transcribe(self.store, config, arguments, turn_id=turn_id,
                    platform=self.platform, slots=self.slots, direct=direct, notify=lambda: self.on_update(scene))
            except BaseException as error:
                with self.store.db:
                    self.store.db.execute("UPDATE audio_cache SET status=?,error=?,updated=? "
                        "WHERE scene=? AND platform_id=? AND audio_index=? "
                        "AND (status='running' OR (? AND status='queued'))",
                        ("interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
                         f"{type(error).__name__}: {error}", self.store.now(), *position, automatic))
                raise
            finally:
                self.on_update(scene)
