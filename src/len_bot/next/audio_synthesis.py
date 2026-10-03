"""Reserved speech-synthesis contract; providers and platform sending are separate."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SynthesisInput:
    text: str
    voice: str


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    """Reference an actual audio file managed by the instance's media/file storage."""

    media: Path
    mime_type: str
    duration_seconds: float | None = None


class SpeechSynthesizer(Protocol):
    async def synthesize(self, request: SynthesisInput) -> SynthesizedAudio: ...
