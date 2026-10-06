"""One scene's chat in a time window: local counts, topics from the model in batches, quotes checked against the store.

The model only ever sees message numbers it can cite; every quote on the card is the stored message itself.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from len_bot.next.plugin import PluginContext

PROMPTS = Path(__file__).with_name("prompts")
PAGE = 500
MAX_MESSAGES = 5000
MAX_LINE = 300
MAX_TOPICS = 5
MAX_QUOTES = 3


@dataclass(frozen=True)
class Line:
    index: int
    time: float
    uid: str
    name: str
    text: str
    is_self: bool


@dataclass(frozen=True)
class Topic:
    title: str
    summary: str
    people: tuple[str, ...]


@dataclass(frozen=True)
class Quote:
    line: Line
    comment: str


@dataclass(frozen=True)
class Summary:
    after: float
    before: float
    timezone: str
    total: int
    speakers: int
    top: tuple[tuple[str, int], ...]
    hours: tuple[tuple[int, int], ...]   # (hour of day, messages) in window order
    headline: str
    topics: tuple[Topic, ...]
    quotes: tuple[Quote, ...]
    batches: int

    def window_text(self) -> str:
        zone = ZoneInfo(self.timezone)
        start, end = datetime.fromtimestamp(self.after, zone), datetime.fromtimestamp(self.before, zone)
        return f"{start.month}月{start.day}日 {start:%H:%M} – {end.month}月{end.day}日 {end:%H:%M}"

    def peak(self) -> tuple[int, int] | None:
        busiest = max(self.hours, key=lambda item: item[1], default=None)
        return None if busiest is None or busiest[1] == 0 else busiest

    def description(self) -> str:
        """Plain text of the card for the chat context; the image itself is not read back."""
        lines = [f"群聊总结卡片（{self.window_text()}，{self.total} 条消息，{self.speakers} 人发言）：{self.headline}"]
        lines += [f"{index}. {topic.title}：{topic.summary}" for index, topic in enumerate(self.topics, 1)]
        lines += [f"原话：{quote.line.name}「{quote.line.text}」" for quote in self.quotes]
        return "\n".join(lines)


def message_text(message) -> str:
    parts = []
    for segment in message.segments:
        data = segment.data
        if segment.type == "text":
            parts.append(str(data.get("text", "")))
        elif segment.type == "at":
            parts.append("@" + str(data.get("name") or data.get("qq") or ""))
        elif segment.type == "image":
            parts.append("[图片]")
        elif segment.type == "face":
            parts.append("[表情]")
        elif segment.type == "record":
            parts.append("[语音]")
        elif segment.type != "reply":
            parts.append(f"[{segment.type}]")
    return " ".join("".join(parts).split())


def read_window(ctx: PluginContext, scene: str, after: float, before: float) -> list[Line]:
    """Received and actually sent messages, oldest first; only the newest MAX_MESSAGES are kept."""
    messages, offset = [], 0
    while True:
        page = ctx.messages_between(scene, after, before, offset=offset, limit=PAGE)
        messages += page
        offset += len(page)
        if len(page) < PAGE:
            break
    # Commands to bots (/群日报 and the like) are not conversation.
    kept = [message for message in messages
            if not message.recalled and message.send_status in ("received", "sent")
            and message_text(message) and not message_text(message).startswith("/")]
    return [Line(index, message.time, message.sender.uid, message.sender.card or message.sender.nickname or message.sender.uid,
                 message_text(message), message.is_self)
            for index, message in enumerate(kept[-MAX_MESSAGES:], 1)]


def render(line: Line, zone: ZoneInfo) -> str:
    text = line.text if len(line.text) <= MAX_LINE else line.text[:MAX_LINE] + "…"
    who = line.name + ("（Bot）" if line.is_self else "")
    return f"[{line.index}] {datetime.fromtimestamp(line.time, zone):%H:%M} {who}：{text}"


def batches(lines: list[Line], zone: ZoneInfo, budget: int) -> list[str]:
    """Consecutive transcript slices of at most ``budget`` characters each."""
    result, current, used = [], [], 0
    for line in lines:
        text = render(line, zone)
        if current and used + len(text) + 1 > budget:
            result.append("\n".join(current))
            current, used = [], 0
        current.append(text)
        used += len(text) + 1
    if current:
        result.append("\n".join(current))
    return result


def parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if match is None:
        raise ValueError("模型没有返回 JSON 对象：" + text[:200])
    return json.loads(match[0])


def counts(lines: list[Line], after: float, zone: ZoneInfo) -> dict:
    people = [line for line in lines if not line.is_self]
    speakers = Counter(line.uid for line in people)
    names = {line.uid: line.name for line in people}
    per_hour = Counter(datetime.fromtimestamp(line.time, zone).hour for line in lines)
    first = datetime.fromtimestamp(after, zone).hour
    return {"total": len(lines), "speakers": len(speakers),
            "top": tuple((names[uid], count) for uid, count in speakers.most_common(5)),
            "hours": tuple(((first + step) % 24, per_hour[(first + step) % 24]) for step in range(24))}


async def summarize(ctx: PluginContext, scene: str, lines: list[Line], after: float, before: float, *,
                    role: str, budget: int) -> Summary:
    timezone = ctx.timezone(scene)
    zone = ZoneInfo(timezone)
    stats = counts(lines, after, zone)
    overview = {"消息数": stats["total"], "发言人数": stats["speakers"],
                "说话最多": [f"{name} {count} 条" for name, count in stats["top"]]}
    parts = batches(lines, zone, budget)
    if len(parts) == 1:
        material = {"统计": overview, "群聊记录": parts[0]}
    else:
        # Each batch is read once; only its notes, with message numbers, reach the final call.
        chunk = (PROMPTS / "daily_chunk.md").read_text(encoding="utf-8")
        notes = []
        for number, part in enumerate(parts, 1):
            raw = await ctx.generate(scene, json.dumps({"第几段": f"{number}/{len(parts)}", "群聊记录": part},
                                                       ensure_ascii=False), role=role, system=chunk)
            notes.append(parse(raw))
        material = {"统计": overview, "各段整理": notes}
    final = parse(await ctx.generate(scene, json.dumps(material, ensure_ascii=False), role=role,
                                     system=(PROMPTS / "daily_final.md").read_text(encoding="utf-8")))
    by_index = {line.index: line for line in lines}
    known = {line.name for line in lines}
    topics = tuple(Topic(str(item.get("title", "")).strip()[:40], str(item.get("summary", "")).strip()[:300],
                         tuple(name for name in item.get("people", []) if name in known)[:6])
                   for item in final.get("topics", [])[:MAX_TOPICS]
                   if str(item.get("title", "")).strip() and str(item.get("summary", "")).strip())
    quotes, seen = [], set()
    for item in final.get("quotes", []):
        line = by_index.get(item.get("index")) if isinstance(item.get("index"), int) else None
        if line is None or line.is_self or line.index in seen:
            continue
        seen.add(line.index)
        quotes.append(Quote(line, str(item.get("comment", "")).strip()[:80]))
        if len(quotes) == MAX_QUOTES:
            break
    headline = str(final.get("headline", "")).strip()[:60]
    if not topics or not headline:
        raise ValueError("模型返回的总结缺少标题或话题")
    return Summary(after, before, timezone, stats["total"], stats["speakers"], stats["top"], stats["hours"],
                   headline, topics, tuple(quotes), len(parts))
