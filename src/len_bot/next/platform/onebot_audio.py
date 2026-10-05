"""OneBot audio retrieval and native WAV response parsing."""

import asyncio
import base64
import binascii
from io import BytesIO
import wave

from ..models.asr import AudioSettings
from .platform_tools import PlatformCall


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



async def fetch_record(call: PlatformCall, source: dict, settings: AudioSettings) -> tuple[bytes, float]:
    file = source.get('file')
    if not isinstance(file, str) or not file.strip():
        raise ValueError(f'Audio segment lacks its actual file locator: {source!r}')
    async with asyncio.timeout(settings.timeout_seconds):
        return parse_record(await call('get_record', {'file': file, 'out_format': 'wav'}), settings)
