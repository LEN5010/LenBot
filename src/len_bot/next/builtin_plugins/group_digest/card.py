"""The daily summary card in the panel's style: neutral surfaces, brand pink only as an accent.

Sizes are logical pixels drawn at 2x. Text falls back through the font chain per character, and a
character no font has (most emoji) is left out rather than drawn as a box.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
import regex

from .daily import Summary

# Panel palette (src/len_bot/web/frontend/src/styles/theme.js).
BRAND, BRAND_SOFT, PRIMARY = "#e799b0", "#fbeff3", "#b4476a"
INK, MUTED, LINE, PAGE, SURFACE, TRACK = "#1d1b20", "#6b6870", "#e9e9ec", "#f7f7f8", "#ffffff", "#ededf0"
TINTS = [("#f8e6ec", "#a8405f"), ("#eee8f6", "#6c4f96"), ("#f8ece0", "#93552a"),
         ("#e4f1ec", "#2a7457"), ("#e6edf6", "#3d6596")]

SCALE = 2
WIDTH = 600
MARGIN = 20
PAD = 24
CARD_GAP = 14

# Checked in order when no font is configured; the host image installs Noto Sans CJK.
SYSTEM_FONTS = {
    # PingFang is a downloaded system asset whose directory name is a hash.
    "darwin": ["/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc",
               "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
               "/System/Library/Fonts/STHeiti Medium.ttc"],
    "linux": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc"],
    "win32": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"],
}


def find_font(configured: str) -> Path:
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise ValueError(f"字体文件不存在：{configured}")
        return path
    for pattern in SYSTEM_FONTS.get(sys.platform, []):
        anchor = Path(pattern).anchor
        found = sorted(Path(anchor).glob(pattern[len(anchor):])) if "*" in pattern else [Path(pattern)]
        if found and found[0].is_file():
            return found[0]
    raise ValueError("没有找到中文字体，请在插件参数里填写字体文件的路径")


def face_index(path: Path) -> int:
    """In a collection prefer the Simplified Chinese regular face, e.g. Noto Sans CJK SC or PingFang SC."""
    if path.suffix.lower() != ".ttc":
        return 0
    for index in range(32):
        try:
            family, style = ImageFont.truetype(str(path), 12, index=index).getname()
        except OSError:
            break
        if "SC" in family.split() and style in ("Regular", "Normal"):
            return index
    return 0


class Fonts:
    def __init__(self, path: Path) -> None:
        self.path, self.index = path, face_index(path)

    @lru_cache(maxsize=32)
    def get(self, size: float) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.path), round(size * SCALE), index=self.index)

    @lru_cache(maxsize=8192)
    def has(self, char: str) -> bool:
        if char.isspace():
            return True
        font = self.get(14)
        signature = lambda text: (font.getmask(text).size, bytes(font.getmask(text)))
        return signature(char) != signature("\U000F0000")


class Painter:
    def __init__(self, fonts: Fonts, height: int = 4000) -> None:
        self.fonts = fonts
        self.image = Image.new("RGB", (WIDTH * SCALE, height * SCALE), PAGE)
        self.draw = ImageDraw.Draw(self.image)

    def clean(self, text: str) -> str:
        return "".join(cluster for cluster in regex.findall(r"\X", text) if self.fonts.has(cluster[0]))

    def width(self, text: str, size: float) -> float:
        return self.fonts.get(size).getlength(text) / SCALE

    def text(self, x: float, y: float, text: str, size: float, color: str = INK, *, bold: bool = False,
             anchor: str = "la") -> None:
        self.draw.text((x * SCALE, y * SCALE), text, font=self.fonts.get(size), fill=color, anchor=anchor,
                       stroke_width=round(size * SCALE * 0.03) if bold else 0, stroke_fill=color)

    def wrap(self, text: str, size: float, width: float, max_lines: int) -> list[str]:
        lines, current = [], ""
        for cluster in regex.findall(r"\X", self.clean(text)):
            if current and self.width(current + cluster, size) > width:
                lines.append(current)
                current = "" if cluster.isspace() else cluster
            else:
                current += cluster
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            while lines[-1] and self.width(lines[-1] + "…", size) > width:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"
        return lines

    def paragraph(self, x: float, y: float, text: str, size: float, width: float, *, color: str = INK,
                  max_lines: int = 6, bold: bool = False, leading: float = 1.65) -> float:
        for line in self.wrap(text, size, width, max_lines):
            self.text(x, y, line, size, color, bold=bold)
            y += size * leading
        return y

    def box(self, top: float, bottom: float, fill: str = SURFACE) -> None:
        self.draw.rounded_rectangle((MARGIN * SCALE, top * SCALE, (WIDTH - MARGIN) * SCALE, bottom * SCALE),
                                    radius=14 * SCALE, fill=fill, outline=LINE, width=SCALE)


def section(painter: Painter, top: float, title: str, body) -> float:
    """Draw a white card: measure the content once on a scratch painter, then for real."""
    scratch = Painter(painter.fonts, height=1)
    height = body(scratch, top + PAD + 30) - top + PAD - 6
    painter.box(top, top + height)
    painter.text(MARGIN + PAD, top + PAD, title, 15, INK, bold=True)
    body(painter, top + PAD + 30)
    return top + height + CARD_GAP


def render(summary: Summary, font: Path, title: str = "群聊总结") -> bytes:
    painter = Painter(Fonts(font))
    inner = WIDTH - 2 * (MARGIN + PAD)
    left = MARGIN + PAD

    # Header: title, window and the one-line headline on a soft brand panel.
    y = MARGIN
    headline = painter.wrap(summary.headline, 18, inner, 3)
    header = PAD + 30 + 24 + 14 + len(headline) * 18 * 1.6 + PAD - 6
    painter.box(y, y + header, BRAND_SOFT)
    painter.draw.ellipse(((left) * SCALE, (y + PAD + 6) * SCALE, (left + 14) * SCALE, (y + PAD + 20) * SCALE), fill=BRAND)
    painter.text(left + 22, y + PAD, title, 22, INK, bold=True)
    painter.text(left, y + PAD + 34, summary.window_text(), 13, MUTED)
    line_y = y + PAD + 34 + 24 + 8
    for line in headline:
        painter.text(left, line_y, line, 18, PRIMARY, bold=True)
        line_y += 18 * 1.6
    y += header + CARD_GAP

    # Three numbers.
    peak = summary.peak()
    tiles = [(str(summary.total), "条消息"), (str(summary.speakers), "人发言"),
             (f"{peak[0]:02d}:00" if peak else "—", "最热闹的时候")]
    painter.box(y, y + 92)
    column = (WIDTH - 2 * MARGIN) / 3
    for index, (value, label) in enumerate(tiles):
        center = MARGIN + column * index + column / 2
        painter.text(center, y + 20, value, 26, INK, bold=True, anchor="ma")
        painter.text(center, y + 58, label, 12, MUTED, anchor="ma")
        if index:
            painter.draw.line(((MARGIN + column * index) * SCALE, (y + 22) * SCALE,
                               (MARGIN + column * index) * SCALE, (y + 70) * SCALE), fill=LINE, width=SCALE)
    y += 92 + CARD_GAP

    def activity(target: Painter, top: float) -> float:
        most = max((count for _, count in summary.hours), default=0) or 1
        slot = inner / 24
        for step, (hour, count) in enumerate(summary.hours):
            x = left + step * slot
            height = max(3, 70 * count / most) if count else 3
            color = PRIMARY if peak and hour == peak[0] else BRAND if count else TRACK
            target.draw.rounded_rectangle(((x + 2) * SCALE, (top + 70 - height) * SCALE,
                                           (x + slot - 2) * SCALE, (top + 70) * SCALE), radius=2 * SCALE, fill=color)
            if step % 6 == 0:
                target.text(x + slot / 2, top + 78, f"{hour:02d}", 11, MUTED, anchor="ma")
        return top + 98
    y = section(painter, y, "什么时候最热闹", activity)

    if summary.top:
        def people(target: Painter, top: float) -> float:
            x, row = left, top
            for index, (name, count) in enumerate(summary.top):
                label = f"{target.clean(name)[:10]}  {count}"
                width = target.width(label, 13) + 24
                if x + width > left + inner:
                    x, row = left, row + 38
                background, ink = TINTS[index % len(TINTS)]
                target.draw.rounded_rectangle((x * SCALE, row * SCALE, (x + width) * SCALE, (row + 28) * SCALE),
                                              radius=14 * SCALE, fill=background)
                target.text(x + 12, row + 6, label, 13, ink, bold=index == 0)
                x += width + 8
            return row + 28
        y = section(painter, y, "说话最多的人", people)

    def topics(target: Painter, top: float) -> float:
        row = top
        for index, topic in enumerate(summary.topics, 1):
            target.draw.ellipse((left * SCALE, (row + 1) * SCALE, (left + 22) * SCALE, (row + 23) * SCALE), fill=BRAND_SOFT)
            target.text(left + 11, row + 5, str(index), 12, PRIMARY, bold=True, anchor="ma")
            row = target.paragraph(left + 32, row + 1, topic.title, 15, inner - 32, bold=True, max_lines=2, leading=1.5)
            row = target.paragraph(left + 32, row + 2, topic.summary, 13.5, inner - 32, color="#3a373e", max_lines=5)
            if topic.people:
                row = target.paragraph(left + 32, row, "参与：" + "、".join(topic.people), 12, inner - 32,
                                       color=MUTED, max_lines=1)
            row += 14
        return row - 14
    y = section(painter, y, "聊了什么", topics)

    if summary.quotes:
        zone = ZoneInfo(summary.timezone)

        def quotes(target: Painter, top: float) -> float:
            row = top
            for quote in summary.quotes:
                start = row
                row = target.paragraph(left + 16, row, quote.line.text, 15, inner - 16, max_lines=4)
                stamp = datetime.fromtimestamp(quote.line.time, zone)
                row = target.paragraph(left + 16, row + 2, f"{quote.line.name} · {stamp:%H:%M}", 12, inner - 16,
                                       color=MUTED, max_lines=1)
                if quote.comment:
                    row = target.paragraph(left + 16, row, quote.comment, 13, inner - 16, color=PRIMARY, max_lines=2)
                target.draw.rounded_rectangle((left * SCALE, (start + 2) * SCALE, (left + 4) * SCALE, (row - 6) * SCALE),
                                              radius=2 * SCALE, fill=BRAND)
                row += 12
            return row - 12
        y = section(painter, y, "原话", quotes)

    painter.text(WIDTH / 2, y + 4, "LenBot", 11, MUTED, anchor="ma")
    height = y + 28
    output = BytesIO()
    painter.image.crop((0, 0, WIDTH * SCALE, round(height * SCALE))).save(output, "PNG", optimize=True)
    return output.getvalue()
