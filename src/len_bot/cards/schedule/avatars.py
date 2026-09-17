"""Rotate through a member's sticker folder instead of reusing one avatar.

Ported from the reference plugin's `_select_avatar_paths`: draw from the
stickers not used yet, and only once a member's folder is exhausted start it
over. The used-set lives on the rotation object rather than per render, so
consecutive cards differ too -- which is what makes it a rotation and not just
a random pick that repeats every few pushes.
"""
from __future__ import annotations

import base64
import mimetypes
import random
from pathlib import Path

IMAGE_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp', '.gif'})
MAX_STICKER_BYTES = 2 * 1024 * 1024


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
    try:
        payload = path.read_bytes()
    except OSError:
        return ''
    if not payload or len(payload) > MAX_STICKER_BYTES:
        return ''
    kind = mimetypes.guess_type(path.name)[0] or 'image/png'
    return f'data:{kind};base64,' + base64.b64encode(payload).decode('ascii')


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
