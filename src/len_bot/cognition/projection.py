"""Model-facing text projection helpers (ADR-0037/0038).

Pure functions shared by the Social Core and history retrieval: CQ payload
compaction applies ONLY to model-facing projections — raw events stay
immutable — plus token estimation and newest-first budgeted packing.
"""

import math
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from len_bot.events.models import Event, EventType


def project_onebot_text(text: str) -> str:
    labels = {
        "image": "图片：尚未解析",
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
    names = f" 账号昵称={sender.get('nickname', '未知')} 群名片={sender.get('card') or '未设置'}" if sender else ""
    onebot_message_id = event.payload.get("message_id")
    message_ref = (
        f"EventID={event.id} OneBotMessageID={onebot_message_id}"
        if onebot_message_id is not None
        else f"EventID={event.id}"
    )
    text = project_onebot_text(event.raw_text)
    quote = event.metadata.get("quote_context")
    if quote is not None:
        text += "\n引用原话：" + ("本群历史中未找到，不能猜测作者或内容" if quote.get("missing") else
            f"{quote['actor_id']} EventID={quote['event_id']}: {project_onebot_text(quote['text'])}")
    if event.event_type == EventType.MESSAGE_SEND_FAILED:
        text += "\n发送未确认：" + event.payload.get("error", "旧记录缺少详细原因")
    if event.metadata.get("reflection_stale"):
        text += "\n这份反思读取的是旧版本理解，尚未应用；记忆回执是当时的结果，使用前需核对当前 query_memory，不能覆盖新认识。"
    if event.metadata.get("obsolete_job_result"):
        text += "\n这是旧目标的工作事件，当前版本已变更或取消，不能用它确认当前交付。"
    if event.event_type in {EventType.REFLECTION_RECORDED, EventType.TASK_REVIEW, EventType.TASK_DUE, EventType.TOOL_COMPLETED, EventType.AGENT_JOB_FINISHED, EventType.AGENT_JOB_PROGRESS}:
        text += "\n运行时事项（反思内容仍是待核对提案）：" + json.dumps(
            {k: v for k, v in event.payload.items() if k not in {"raw_text", "content"}}, ensure_ascii=False)
    return f"[{datetime.fromtimestamp(event.timestamp, ZoneInfo('Asia/Shanghai')).isoformat()} {event.event_type.value} {message_ref}] {actor}({event.actor_id}){names}: {text}"


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    # Deliberately approximate: CJK segmentation varies by provider. Actual usage
    # is recorded beside this estimate on every model step.
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 3)


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
