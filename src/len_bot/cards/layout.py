"""Text measurement and semantic page splitting used by Pillow cards."""
from __future__ import annotations

from collections.abc import Iterable


def text_lines(draw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
        lines.append(current)
    return lines


def measure_text(draw, text: str, font, width: int, line_height: int) -> tuple[list[str], int]:
    lines = text_lines(draw, text, font, width)
    return lines, max(line_height, len(lines) * line_height)


def split_pages(items: Iterable[object], page_height: int, item_height, *, keep_one: bool = True) -> list[list[object]]:
    """Split semantic items without splitting an item across pages."""
    pages: list[list[object]] = []
    current: list[object] = []
    used = 0
    for item in items:
        height = max(1, int(item_height(item)))
        if current and used + height > page_height:
            pages.append(current)
            current, used = [], 0
        current.append(item)
        used += height
    if current or not pages and keep_one:
        pages.append(current)
    return pages
