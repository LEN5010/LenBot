"""Read a fixed saved-message window; topic interpretation stays in work."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from len_bot.cognition.jobs import GroupSummaryRange
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolNextCall, ToolResult, ToolSource

from .config import GroupSummaryConfig


class WindowCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    revision: int = Field(ge=1)
    after_rowid: int = Field(ge=0)


class GroupSummaryService:
    def __init__(self, event_store, config: GroupSummaryConfig):
        self.event_store = event_store
        self.config = config

    @staticmethod
    def current_continuation(job, observation):
        """Project a saved page locator into the unchanged, current work scope."""
        if observation.coverage!='group_summary_window' or observation.source_next_call is None:
            return None
        if observation.source_next_call.name!='read_group_chat_window':
            raise ValueError('Saved summary page has a foreign continuation tool')
        header=json.loads(observation.content.split('\n',1)[0])
        original=GroupSummaryRange.model_validate(header['range'])
        current=GroupSummaryRange.model_validate(job['summary_range'])
        fields=('start_at','end_at','snapshot_rowid','bot_actor_id')
        if header['job_id']!=job['id'] or any(getattr(original,key)!=getattr(current,key) for key in fields):
            return None
        position=WindowCursor.model_validate_json(observation.source_next_call.arguments['cursor'])
        if position.job_id!=job['id'] or position.revision!=header['job_revision']:
            raise ValueError('Saved summary continuation does not match its source page')
        return ToolNextCall(name='read_group_chat_window',arguments={'cursor':WindowCursor(
            job_id=job['id'],revision=job['revision'],after_rowid=position.after_rowid).model_dump_json()})

    async def read_window(self, cursor: str | None, call: PluginCallContext) -> ToolResult:
        if call.role != "work" or not call.job_id:
            raise ValueError("群原话窗口只在已提交的总结工作中读取")
        job = await self.event_store.get_job(call.job_id, call.scene_id)
        if job is None or job["work_operation"] != "group_summary" or job["status"] != "processing":
            raise ValueError("当前场景没有对应的运行中总结工作")
        request = GroupSummaryRange.model_validate(job["summary_range"])
        if call.cutoff_rowid != request.snapshot_rowid or call.requester_qq_uid != job["requester_qq_uid"]:
            raise ValueError("总结读取上下文与已保存的请求者或快照不一致")
        after_rowid = 0
        if cursor is not None:
            position = WindowCursor.model_validate_json(cursor)
            if position.job_id != job["id"] or position.revision != job["revision"]:
                raise ValueError("游标不属于当前工作版本；使用当前工作observation_catalog给出的续页位置，范围改变且无可用续页时才从cursor=null开始")
            after_rowid = position.after_rowid
        rows = await self.event_store.group_summary_messages(
            call.scene_id, start_at=request.start_at.timestamp(), end_at=request.end_at.timestamp(),
            cutoff_rowid=request.snapshot_rowid, bot_actor_id=request.bot_actor_id,
            after_rowid=after_rowid, limit=self.config.page_messages + 1)
        has_more = len(rows) > self.config.page_messages
        selected = rows[:self.config.page_messages]
        next_cursor = WindowCursor(job_id=job["id"], revision=job["revision"],
            after_rowid=selected[-1].metadata["_rowid"]).model_dump_json() if has_more else None
        statistics = {key: job["summary_coverage"][key]
                      for key in ("matched_messages", "participants", "matched_characters")}
        header = {"page_type":"group_summary_window", "job_id":job["id"],
                  "job_revision":job["revision"], "range":request.model_dump(mode="json"),
                  "statistics":statistics, "next_cursor":next_cursor,
                  "scope":"本群已保存的人类消息；排除Bot回声、内部事件与模拟数据，包含日程命令和引用评论。",
                  "coverage_note":"本页取回不等于原文已读；先用read_tool_result完整采用本页正文，再用source_next_call取得下一批。不得按页首游标跳过未采用正文，不能仅凭第一页宣称全时段完成。"}
        lines = [json.dumps(header, ensure_ascii=False)]
        sources = []
        for event in selected:
            sender = event.payload.get("sender") or {}
            record = {"event_id":event.id, "rowid":event.metadata["_rowid"],
                      "actor_id":event.actor_id, "timestamp":event.timestamp,
                      "display_name":sender.get("card") or sender.get("nickname") or event.actor_id,
                      "text":event.raw_text, "reply_to_message_id":event.payload.get("reply_to_message_id"),
                      "media_refs":[item["asset_id"] for item in event.metadata.get("media", []) if item.get("asset_id")]}
            lines.append(json.dumps(record, ensure_ascii=False))
            sources.append(ToolSource(event_id=event.id, title=f"本群消息 {event.id}"))
        return ToolResult(status="ok" if selected or not statistics["matched_messages"] else "no_results",
                          content="\n".join(lines), sources=sources, coverage="group_summary_window",
                          evidence_kind="retrieval", source_next_call=ToolNextCall(name='read_group_chat_window',
                              arguments={'cursor':next_cursor}) if next_cursor is not None else None)
