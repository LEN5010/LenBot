"""Resolve one saved voice-message position and reuse its actual WAV and successful transcript."""
from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
import wave

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .asr_model import ASRProtocolError, AudioSettings, transcribe_audio
from .model_slots import ModelSlots
from .platform_tools import PlatformCall
from .store import Store, encode

if TYPE_CHECKING:
    from .config import LabConfig

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_transcribe.md"


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


def parse_record(raw: object, settings: AudioSettings) -> tuple[bytes, float]:
    """NapCat get_record(out_format=wav) returns converted bytes in data.base64."""
    try:
        if not isinstance(raw, dict) or raw.get("status") != "ok" or raw.get("retcode") != 0:
            raise ValueError("get_record did not return status=ok, retcode=0")
        encoded = raw["data"]["base64"]
        if not isinstance(encoded, str) or not encoded:
            raise ValueError("get_record data.base64 must be nonempty text")
        if len(encoded) > 4 * ((settings.max_bytes + 2) // 3):
            raise ValueError(f"record exceeds {settings.max_bytes} bytes")
        wav = base64.b64decode(encoded, validate=True)
        if len(wav) > settings.max_bytes:
            raise ValueError(f"record exceeds {settings.max_bytes} bytes")
        with wave.open(BytesIO(wav), "rb") as stream:
            frames, rate = stream.getnframes(), stream.getframerate()
            if frames <= 0 or rate <= 0:
                raise ValueError("WAV contains no audio frames or sample rate")
            duration = frames / rate
            if duration > settings.max_seconds:
                raise ValueError(f"record duration {duration:g}s exceeds {settings.max_seconds:g}s; not truncated")
            expected = frames * stream.getnchannels() * stream.getsampwidth()
            if expected > settings.max_bytes or len(stream.readframes(frames)) != expected:
                raise ValueError("WAV frames are truncated or exceed configured byte limit")
        return wav, duration
    except (KeyError, TypeError, ValueError, binascii.Error, wave.Error, EOFError) as error:
        # Do not echo megabytes of binary audio as a protocol error.
        fragment = repr(raw)[:500]
        raise ValueError(f"get_record audio parse failed: {error}; raw={fragment}") from error


async def execute_transcribe(store: Store, config: LabConfig, arguments: TranscribeArguments, *,
                             turn_id: str, platform: PlatformCall, slots: ModelSlots | None,
                             direct: bool, notify) -> str:
    scene = config.scene
    message = store.find_message(scene, arguments.message)
    if message is None:
        raise ValueError(f"当前场景没有平台消息 {arguments.message}")
    records = [segment for segment in message.segments if segment.type == "record"]
    if arguments.audio > len(records):
        raise ValueError(f"消息 {arguments.message} 只有 {len(records)} 段语音，没有第 {arguments.audio} 段")
    position = (scene, arguments.message, arguments.audio)
    row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                           position).fetchone()
    audio_reused = row is not None
    if row is None:
        if arguments.refresh:
            raise ValueError("该语音尚未保存音频；先用refresh=false获取，不能只重新识别")
        source = records[arguments.audio - 1].data
        file = source.get("file")
        if not isinstance(file, str) or not file.strip():
            raise ValueError(f"record段缺少实际file字段：{repr(source)[:300]}")
        async with asyncio.timeout(config.audio.timeout_seconds):
            raw = await platform("get_record", {"file": file, "out_format": "wav"})
            wav, duration = parse_record(raw, config.audio)
        with store.db:
            store.db.execute("INSERT INTO audio_cache(scene,platform_id,audio_index,wav,duration,fetched_at) "
                             "VALUES (?,?,?,?,?,?)", (*position, wav, duration, store.now()))
        row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                               position).fetchone()
    reused = row["transcript"] is not None and not arguments.refresh
    if not reused:
        binding = config.models.roles.asr
        provider = config.models.providers[binding.provider]
        async with (slots.slot(direct=direct) if slots is not None else nullcontext()):
            call_id = store.start_call(turn_id, "asr", {
                "settings": binding.model_dump(mode="json"), "base_url": provider.base_url,
                "file": {"scene": scene, "platform_message_id": arguments.message, "audio": arguments.audio,
                         "content_type": "audio/wav", "bytes": len(row["wav"]), "duration": row["duration"],
                         "source": "audio_cache"}, "response_format": "json", "price": None,
            })
            notify()
            try:
                reply = await transcribe_audio(binding, base_url=provider.base_url, api_key=provider.api_key, wav=row["wav"])
            except BaseException as error:
                store.end_call(call_id, error.response if isinstance(error, ASRProtocolError) else None,
                               None, f"{type(error).__name__}: {error}")
                notify()
                raise
            store.end_call(call_id, reply.response, reply.usage)
            with store.db:
                store.db.execute("UPDATE audio_cache SET transcript=?,provider=?,model=?,transcribed_at=? "
                    "WHERE scene=? AND platform_id=? AND audio_index=?",
                    (reply.text, binding.provider, binding.model, store.now(), *position))
            notify()
        row = store.db.execute("SELECT * FROM audio_cache WHERE scene=? AND platform_id=? AND audio_index=?",
                               position).fetchone()
    result = {key: row[key] for key in ("duration", "fetched_at", "transcript", "provider", "model", "transcribed_at")}
    result.update(scene=scene, platform_message_id=arguments.message, audio=arguments.audio,
                  wav_bytes=len(row["wav"]), audio_reused=audio_reused, transcript_reused=reused)
    return encode(result) + "\n" + PROMPT.read_text(encoding="utf-8").strip()
