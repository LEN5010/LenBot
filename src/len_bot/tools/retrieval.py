import json
import logging
from typing import Any, Optional
from len_bot.events.store import EventStore
from len_bot.memory.store import MemoryStore
from len_bot.events.models import Event
from len_bot.cognition.projection import project_event

logger = logging.getLogger(__name__)

class RetrievalToolkit:
    """Agentic History & Memory Retrieval Tools (ADR-0010 & ADR-0011) enforcing Ambient ExecutionScope."""
    
    def __init__(
        self,
        event_store: EventStore,
        allowed_scopes: list[str],
        default_scene_id: str,
        memory_store: Optional[MemoryStore] = None,
        plugin_host: Optional[Any] = None,
        bot_qq: int | str = "",
    ):
        self.event_store = event_store
        self.allowed_scopes = allowed_scopes
        self.default_scene_id = default_scene_id
        self.memory_store = memory_store
        self.plugin_host = plugin_host
        self.bot_qq = bot_qq

    async def _project_rows(self, rows: list[dict]) -> list[str]:
        events = [Event.model_validate(row) for row in rows]
        projected = {}
        for scene_id in dict.fromkeys(event.scene_id for event in events):
            enriched = await self.event_store.project_reply_context(
                scene_id, [event for event in events if event.scene_id == scene_id])
            projected.update({event.id: project_event(event, self.bot_qq) for event in enriched})
        return [projected[event.id] for event in events]

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        tools = [
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
            },
            {
                "type": "function",
                "function": {
                    "name": "query_memory",
                    "description": "查询当前合法范围内对特定人物、群体或话题已形成的认识记忆（L2 认知信念），支持全局跨场景偏好检索与演进历史追溯。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subject": {
                                "type": "string",
                                "description": "认识对象的主体，如'user:1001'或'group:123'。"
                            },
                            "kind": {
                                "type": "string",
                                "description": "记忆种类：preference（偏好）、habit（习惯）、relationship（关系）、fact（事实）、group_norm（群体规范）、topic_interest（话题兴趣）、recurring_role（常扮演角色）、social_pattern（社交模式）。"
                            },
                            "key": {
                                "type": "string",
                                "description": "特定槽位键，例如'food'、'programming_language'、'sleep_schedule'等。"
                            },
                            "query": {
                                "type": "string",
                                "description": "模糊检索断言内容的子串或关键词，例如'Rust'、'火锅'等。"
                            },
                            "include_history": {
                                "type": "boolean",
                                "description": "是否包含已撤销（refuted）或被替代（superseded）的历史信念及其纠正证据，默认 false。"
                            }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_episode",
                    "description": "读取过去某次完整经历（L1 Episode）的摘要、参与人和引用的原始事件来源。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "episode_id": {
                                "type": "string",
                                "description": "经历事件的 ID（如 ep_rec_xxx）。"
                            }
                        },
                        "required": ["episode_id"]
                    }
                }
            }
        ]
        if self.plugin_host:
            tools.extend(self.plugin_host.get_tool_definitions())
        return tools

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            if self.plugin_host and self.plugin_host.has_tool(tool_name):
                return await self.plugin_host.execute_tool(tool_name, arguments)

            if tool_name == "search_messages":
                query = arguments.get("query", "")
                limit = int(arguments.get("limit", 10))
                rows = await self.event_store.search_messages(
                    query=query,
                    allowed_scopes=self.allowed_scopes,
                    limit=limit
                )
                formatted = await self._project_rows(rows)
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
                formatted = await self._project_rows(rows)
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
                formatted = await self._project_rows(rows)
                return "\n".join(formatted) if formatted else "该时间段内无历史记录。"

            elif tool_name == "query_person_history":
                actor_id = arguments.get("actor_id", "")
                limit = int(arguments.get("limit", 10))
                rows = await self.event_store.query_person_history(
                    actor_id=actor_id,
                    allowed_scopes=self.allowed_scopes,
                    limit=limit
                )
                formatted = await self._project_rows(rows)
                return "\n".join(formatted) if formatted else f"未找到用户 {actor_id} 的历史发言。"

            elif tool_name == "query_memory":
                if not self.memory_store:
                    return "未配置记忆库。"
                subject = arguments.get("subject")
                kind = arguments.get("kind")
                key = arguments.get("key")
                query = arguments.get("query")
                include_history = bool(arguments.get("include_history", False))
                memories = await self.memory_store.query_memories(
                    allowed_scopes=self.allowed_scopes,
                    subject=subject,
                    kind=kind,
                    key=key,
                    query=query,
                    include_superseded=include_history
                )
                formatted = []
                for m in memories:
                    status_tag = f"[{m.status.value.upper()}]"
                    history_tag = f" (已被覆盖 superseded_by: {m.superseded_by})" if m.status.value == "superseded" else ""
                    formatted.append(
                        f"[{m.id}] {status_tag} [{m.kind.upper()}] {m.subject} -> {m.key}: {m.value} "
                        f"(certainty: {m.certainty.value}, assertion: {m.human_readable_assertion}{history_tag})"
                        f" 原始证据EventIDs={m.evidence} 修订原因={m.revision_reason} 修订证据={m.revision_evidence}"
                    )
                return "\n".join(formatted) if formatted else "未找到匹配的认识信念记忆。"

            elif tool_name == "inspect_episode":
                if not self.memory_store:
                    return "未配置经历库。"
                episode_id = arguments.get("episode_id", "")
                ep = await self.memory_store.get_episode_in_scopes(
                    episode_id,
                    self.allowed_scopes,
                )
                if not ep:
                    return f"在当前可用范围内未找到经历记录 {episode_id}。"
                return (
                    f"【经历: {ep.title}】 (ID: {ep.id})\n"
                    f"时间: {ep.created_at} | 参与者: {', '.join(ep.participants)}\n"
                    f"标签: {', '.join(ep.tags)}\n"
                    f"摘要: {ep.summary}\n"
                    f"引用原始事件数: {len(ep.source_event_ids)}"
                )

            return f"未知工具: {tool_name}"
        except Exception as e:
            logger.exception("Error executing tool %s: %s", tool_name, e)
            return f"工具执行失败: {e}"
