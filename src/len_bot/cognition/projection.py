"""Model-facing text projection helpers (ADR-0037/0038).

Pure functions shared by the FULL and FAST context assemblers: CQ payload
compaction applies ONLY to model-facing projections — raw events stay
immutable — plus token estimation and newest-first budgeted packing.
"""

import math
import re

from len_bot.events.models import Event


def project_onebot_text(text: str) -> str:
    labels = {
        "image": "图片",
        "record": "语音",
        "video": "视频",
        "face": "表情",
        "reply": "回复消息",
        "at": "提及",
    }

    def replace(match: re.Match[str]) -> str:
        cq_type = match.group(1)
        payload = match.group(2) or ""
        label = labels.get(cq_type, f"CQ:{cq_type}")
        if cq_type == "at":
            qq_match = re.search(r"(?:^|,)qq=([^,]+)", payload)
            return f"[提及 QQ {qq_match.group(1)}]" if qq_match else "[提及成员]"
        if cq_type == "reply":
            id_match = re.search(r"(?:^|,)id=([^,]+)", payload)
            return f"[回复消息 {id_match.group(1)}]" if id_match else "[回复消息]"
        return f"[{label}]"

    return re.sub(r"\[CQ:([a-zA-Z0-9_-]+)(?:,([^\]]*))?\]", replace, text)


def project_event(event: Event, bot_qq: int | str) -> str:
    sender = event.payload.get("sender") or {}
    display_name = sender.get("card") or sender.get("nickname")
    actor = "你(Bot)" if event.actor_id == f"user:{bot_qq}" else (display_name or event.actor_id)
    onebot_message_id = event.payload.get("message_id")
    message_ref = (
        f"EventID={event.id} OneBotMessageID={onebot_message_id}"
        if onebot_message_id is not None
        else f"EventID={event.id}"
    )
    return f"[{message_ref}] {actor}({event.actor_id}): {project_onebot_text(event.raw_text)}"


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    return cjk + math.ceil((len(text) - cjk) / 4)


def pack_recent_chat(raw_events: list[Event], token_budget: int, bot_qq: int | str) -> list[str]:
    """Newest-first packing; the oldest edge rolls out only when the budget forces it."""
    selected: list[str] = []
    used = 0
    for event in reversed(raw_events):
        if not event.raw_text:
            continue
        line = project_event(event, bot_qq)
        cost = estimate_tokens(line) + 1
        if used + cost > token_budget:
            break
        selected.append(line)
        used += cost
    selected.reverse()
    return selected
