"""Rotate through a member's sticker folder instead of reusing one avatar.

Ported from the reference plugin's `_select_avatar_paths`: draw from the
stickers not used yet, and only once a member's folder is exhausted start it
over. The used-set lives on the rotation object rather than per render, so
consecutive cards differ too -- which is what makes it a rotation and not just
a random pick that repeats every few pushes.
"""
from __future__ import annotations

import base64
import io
import random
from pathlib import Path

from PIL import Image

IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif'})
MAX_STICKER_BYTES = 2 * 1024 * 1024
# The schedule template draws avatars at 46 CSS pixels; 2x is enough for the
# screenshot and keeps the inlined HTML small. Original stickers are ~650KB.
AVATAR_DISPLAY_PX = 46
INLINE_PX = AVATAR_DISPLAY_PX * 2


def sticker_candidates(directory) -> list[Path]:
    """Every usable sticker in one member's folder, in a stable order."""
    if not directory:
        return []
    root = Path(directory)
    if not root.is_dir():
        return [root] if root.is_file() else []
    return sorted((path for path in root.rglob('*')
                   if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES),
                  key=lambda path: str(path).casefold())


def as_data_uri(path: Path) -> str:
    """One sticker as a PNG data URI, already sized for the 46px avatar."""
    try:
        payload = _inline_png_bytes(path)
    except (OSError, Image.UnidentifiedImageError, ValueError):
        return ''
    if not payload:
        return ''
    return 'data:image/png;base64,' + base64.b64encode(payload).decode('ascii')


def _inline_png_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_STICKER_BYTES:
        return b''
    with Image.open(io.BytesIO(raw)) as image:
        frame = image.convert('RGBA')
        frame.thumbnail((INLINE_PX, INLINE_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        frame.save(out, format='PNG', optimize=True)
        return out.getvalue()


class AvatarRotation:
    def __init__(self, directories=None, *, choose=random.choice):
        self._candidates = {name: sticker_candidates(path)
                            for name, path in (directories or {}).items()}
        self._used: dict[str, set] = {}
        self._choose = choose

    def configured(self) -> bool:
        return any(self._candidates.values())

    def pick(self, member: str) -> str:
        """One sticker for this member, as a data: URI ('' when none apply)."""
        candidates = self._candidates.get(member or '')
        if not candidates:
            return ''
        used = self._used.setdefault(member, set())
        available = [path for path in candidates if path not in used]
        if not available:
            used.clear()
            available = candidates
        chosen = self._choose(available)
        used.add(chosen)
        return as_data_uri(chosen)
