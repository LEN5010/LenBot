import json
import logging
from typing import Any, Optional
from len_bot.events.store import EventStore

logger = logging.getLogger(__name__)

class RetrievalToolkit:
    """Agentic History Retrieval Tools (ADR-0010) enforcing Ambient ExecutionScope (ADR-0006)."""
    
    def __init__(self, event_store: EventStore, allowed_scopes: list[str], default_scene_id: str):
        self.event_store = event_store
        self.allowed_scopes = allowed_scopes
        self.default_scene_id = default_scene_id

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_messages",
                    "description": "通过关键词搜索群聊或私聊历史消息记录（底层基于 SQLite FTS5 全文索引）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "要搜索的关键词或子串，例如'直播'、'几点'、'作业'等。"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最多返回的消息条数，默认 10 条。"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_context",
                    "description": "围绕某条特定消息事件 ID，读取其前后的上下文消息对话，还原当时现场。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "event_id": {
                                "type": "string",
                                "description": "目标事件的唯一 ID。"
                            },
                            "before": {
                                "type": "integer",
                                "description": "读取该消息前多少条记录，默认 3 条。"
                            },
                            "after": {
                                "type": "integer",
                                "description": "读取该消息后多少条记录，默认 3 条。"
                            }
                        },
                        "required": ["event_id"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_timeline",
                    "description": "按时间跨度查询当前 Scene 发生的历史事件流水。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_time": {
                                "type": "number",
                                "description": "起始 Unix 时间戳（秒）。"
                            },
                            "end_time": {
                                "type": "number",
                                "description": "结束 Unix 时间戳（秒）。"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最大条数，默认 15。"
                            }
                        },
                        "required": ["start_time", "end_time"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "query_person_history",
                    "description": "按发言人 ID（如 user:123456）查询其在允许范围内的过往发言历史。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "actor_id": {
                                "type": "string",
                                "description": "目标发言者的 actor_id，例如'user:1001'。"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最大条数，默认 10。"
                            }
                        },
                        "required": ["actor_id"]
                    }
                }
            }
        ]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            if tool_name == "search_messages":
                query = arguments.get("query", "")
                limit = int(arguments.get("limit", 10))
                rows = await self.event_store.search_messages(
                    query=query,
                    allowed_scopes=self.allowed_scopes,
                    limit=limit
                )
                formatted = [
                    f"[{r['id']}] {r['actor_id']} (at {r['timestamp']}): {r['payload'].get('raw_text', '')}"
                    for r in rows
                ]
                return "\n".join(formatted) if formatted else "未找到匹配的历史消息。"

            elif tool_name == "read_context":
                event_id = arguments.get("event_id", "")
                before = int(arguments.get("before", 3))
                after = int(arguments.get("after", 3))
                rows = await self.event_store.read_context(
                    event_id=event_id,
                    before=before,
                    after=after,
                    allowed_scopes=self.allowed_scopes
                )
                formatted = [
                    f"[{r['id']}] {r['actor_id']} (at {r['timestamp']}): {r['payload'].get('raw_text', '')}"
                    for r in rows
                ]
                return "\n".join(formatted) if formatted else f"未找到该事件 {event_id} 或其上下文。"

            elif tool_name == "query_timeline":
                start_time = float(arguments.get("start_time", 0))
                end_time = float(arguments.get("end_time", 0))
                limit = int(arguments.get("limit", 15))
                rows = await self.event_store.query_timeline(
                    scene_id=self.default_scene_id,
                    start_time=start_time,
                    end_time=end_time,
                    allowed_scopes=self.allowed_scopes,
                    limit=limit
                )
                formatted = [
                    f"[{r['id']}] {r['actor_id']} (at {r['timestamp']}): {r['payload'].get('raw_text', '')}"
                    for r in rows
                ]
                return "\n".join(formatted) if formatted else "该时间段内无历史记录。"

            elif tool_name == "query_person_history":
                actor_id = arguments.get("actor_id", "")
                limit = int(arguments.get("limit", 10))
                rows = await self.event_store.query_person_history(
                    actor_id=actor_id,
                    allowed_scopes=self.allowed_scopes,
                    limit=limit
                )
                formatted = [
                    f"[{r['id']}] (at {r['timestamp']}): {r['payload'].get('raw_text', '')}"
                    for r in rows
                ]
                return "\n".join(formatted) if formatted else f"未找到用户 {actor_id} 的历史发言。"

            return f"未知工具: {tool_name}"
        except Exception as e:
            logger.exception("Error executing tool %s: %s", tool_name, e)
            return f"工具执行失败: {e}"
