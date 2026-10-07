"""Measured, paginated text cards for plugins; fonts are supplied by the caller."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from collections.abc import Sequence
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
import regex


@dataclass(frozen=True)
class CardSection:
    title: str
    text: str


@dataclass(frozen=True)
class CardPage:
    data: bytes = field(repr=False)
    text: str


class TextCards:
    """No browser, network, font discovery or rendering fallback.

    All source text is wrapped and paginated, including long names and URLs.
    A document exceeding 32 pages fails before creating any outgoing content.
    """

    WIDTH = 1080
    HEIGHT = 1480
    MARGIN = 64
    TOP = 116
    BOTTOM = 104
    MAX_PAGES = 32

    def __init__(self, font: Path) -> None:
        if not font.is_absolute():
            raise ValueError("card_font 必须是字体文件的绝对路径")
        self.fonts = {size: ImageFont.truetype(str(font), size) for size in (22, 28, 32, 44)}

    def render(self, title: str, subtitle: str, sections: Sequence[CardSection], *, source: str) -> tuple[CardPage, ...]:
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        width = self.WIDTH - 2 * self.MARGIN
        # (actual line, font size, color, advance). Gaps remain layout, not fake text.
        lines: list[tuple[str, int, str, int]] = []
        used_height = 0

        def append_line(text: str, size: int, color: str, advance: int) -> None:
            nonlocal used_height
            used_height += advance
            if used_height > self.MAX_PAGES * (self.HEIGHT - self.TOP - self.BOTTOM):
                raise ValueError(f"卡片超过 {self.MAX_PAGES} 页，请缩小查询范围或读取文字结果")
            lines.append((text, size, color, advance))

        @lru_cache(maxsize=4096)
        def length(text: str, size: int) -> float:
            return measure.textlength(text, font=self.fonts[size])

        def add(text: str, size: int, color: str) -> None:
            font = self.fonts[size]
            ascent, descent = font.getmetrics()
            advance = ascent + descent + 12
            for paragraph in text.split("\n"):
                clusters = regex.findall(r"\X", paragraph.expandtabs(4))
                start = 0
                if not clusters:
                    append_line("", size, color, advance)
                while start < len(clusters):
                    remaining = len(clusters) - start
                    lo, hi = 0, min(64, remaining)
                    while length("".join(clusters[start:start + hi]), size) <= width:
                        lo = hi
                        if hi == remaining:
                            break
                        hi = min(hi * 2, remaining)
                    while lo < hi:
                        middle = (lo + hi + 1) // 2
                        if length("".join(clusters[start:start + middle]), size) <= width:
                            lo = middle
                        else:
                            hi = middle - 1
                    if not lo:
                        raise ValueError(f"单个字素超过卡片宽度：{clusters[start][:40]!r}")
                    append_line("".join(clusters[start:start + lo]), size, color, advance)
                    start += lo
            append_line("", size, color, 18)

        add(title, 44, "#182638")
        if subtitle:
            add(subtitle, 22, "#526175")
        for section in sections:
            if section.title:
                add(section.title, 32, "#17665E")
            if section.text:
                add(section.text, 28, "#263649")
        if source:
            add("来源 / " + source, 22, "#526175")

        pages: list[list[tuple[str, int, str, int]]] = [[]]
        y = self.TOP
        for line in lines:
            if y + line[3] > self.HEIGHT - self.BOTTOM:
                if len(pages) == self.MAX_PAGES:
                    raise ValueError(f"卡片超过 {self.MAX_PAGES} 页，请缩小查询范围或读取文字结果")
                pages.append([])
                y = self.TOP
            pages[-1].append(line)
            y += line[3]
        # A trailing paragraph gap must not create an empty image.
        while len(pages) > 1 and not any(line[0] for line in pages[-1]):
            pages.pop()

        result = []
        for number, page in enumerate(pages, 1):
            height = max(440, self.TOP + sum(line[3] for line in page) + self.BOTTOM)
            image = Image.new("RGB", (self.WIDTH, height), "#F4F7FA")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((24, 24, self.WIDTH - 24, height - 24), radius=28, fill="#FFFFFF")
            draw.rectangle((self.MARGIN, 60, self.MARGIN + 44, 66), fill="#17665E")
            draw.text((self.MARGIN + 62, 48), "LENBOT / 来源卡片", font=self.fonts[22], fill="#526175", anchor="lt")
            y = self.TOP
            for text, size, color, advance in page:
                draw.text((self.MARGIN, y), text, font=self.fonts[size], fill=color, anchor="lt")
                y += advance
            draw.line((self.MARGIN, height - 78, self.WIDTH - self.MARGIN, height - 78), fill="#DCE4ED", width=2)
            draw.text((self.MARGIN, height - 60), f"{number} / {len(pages)}", font=self.fonts[22], fill="#526175", anchor="lt")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            result.append(CardPage(buffer.getvalue(), "\n".join(line[0] for line in page if line[0])))
        return tuple(result)
