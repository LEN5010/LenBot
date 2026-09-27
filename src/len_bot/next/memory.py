"""Selected memory backend, scene-bound tools and transient per-turn recall."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from .memory_embeddings import EmbeddingClient, EmbeddingSettings
from .memory_local import LocalMemory, LocalMemorySettings
from .memory_openviking import OpenVikingMemory, OpenVikingSettings
from .messages import ChatMessage, plain_text
from .store import encode

if TYPE_CHECKING:
    from .config import SharedConfig


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
Scope = Literal["scene", "public"]


class IngestSettings(BaseModel):
    model_config = STRICT
    idle_seconds: float = Field(default=1800, ge=0, allow_inf_nan=False)
    min_messages: int = Field(default=50, ge=1, le=100)
    max_age_seconds: float = Field(default=86400, gt=0, allow_inf_nan=False)
    batch_size: int = Field(default=100, ge=1, le=100)
    max_steps: int = Field(default=8, ge=1, le=30)
    timeout_seconds: float = Field(default=180, gt=0, allow_inf_nan=False)


class RecallSettings(BaseModel):
    model_config = STRICT
    auto_recall: bool = True
    recall_budget_chars: int = Field(default=1500, ge=100, le=12000)
    recall_limit: int = Field(default=5, ge=1, le=20)
    ingest: IngestSettings | None = None


class LocalMemoryConfig(RecallSettings):
    backend: Literal["local"]
    local: LocalMemorySettings


class OpenVikingMemoryConfig(RecallSettings):
    backend: Literal["openviking"]
    openviking: OpenVikingSettings


MemorySettings = Annotated[LocalMemoryConfig | OpenVikingMemoryConfig, Field(discriminator="backend")]


class BrowseMemory(BaseModel):
    model_config = STRICT
    action: Literal["browse"]
    path: str = ""
    scope: Scope = "scene"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ReadMemory(BaseModel):
    model_config = STRICT
    action: Literal["read"]
    path: str = Field(min_length=1)
    scope: Scope = "scene"


class SearchMemory(BaseModel):
    model_config = STRICT
    action: Literal["search"]
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class WriteMemory(BaseModel):
    model_config = STRICT
    action: Literal["write"]
    path: str = Field(min_length=1)
    content: str
    reason: str = Field(min_length=1)


class DeleteMemory(BaseModel):
    model_config = STRICT
    action: Literal["delete", "forget"]
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class HistoryMemory(BaseModel):
    model_config = STRICT
    action: Literal["history"]
    path: str = Field(min_length=1)


MEMORY_ARGUMENTS = TypeAdapter(Annotated[
    BrowseMemory | ReadMemory | SearchMemory | WriteMemory | DeleteMemory | HistoryMemory,
    Field(discriminator="action"),
])
MEMORY_TOOL = {"type": "function", "function": {
    "name": "memory", "description": "浏览、检索、读写当前场景长期记忆；公共分区只读。"
    "普通删除与 forget 的范围由当前后端说明，不能据此宣称聊天和备份已删除。",
    "parameters": {
        "type": "object", "additionalProperties": False, "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["browse", "read", "search", "write", "delete", "forget", "history"]},
            "path": {"type": "string", "description": "除 search 外的相对路径；browse 可为空，其他动作必填。"},
            "scope": {"type": "string", "enum": ["scene", "public"], "description": "仅 browse/read 可选；默认 scene。"},
            "query": {"type": "string", "description": "search 必填的检索文字。"},
            "content": {"type": "string", "description": "write 必填的新完整正文。"},
            "reason": {"type": "string", "description": "write/delete/forget 必填的实际原因。"},
            "offset": {"type": "integer", "minimum": 0, "description": "仅 browse 的分页起点，默认 0。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                      "description": "browse 默认 20、最多 100；search 默认 5、最多 20。"},
        },
    },
}}


class MemoryService:
    def __init__(self, settings: LocalMemoryConfig | OpenVikingMemoryConfig,
                 backend: LocalMemory | OpenVikingMemory):
        self.settings, self.backend = settings, backend
        # A complete read/generate/write extraction shares this queue with edits.
        self.write_locks: dict[str, asyncio.Lock] = {}
        self.pending_native_tasks: dict[str, str] = {}

    def write_lock(self, scene: str) -> asyncio.Lock:
        return self.write_locks.setdefault(scene, asyncio.Lock())

    @property
    def actions(self) -> list[str]:
        common = ["browse", "read", "search", "write", "delete"]
        if isinstance(self.backend, LocalMemory):
            common.append("history")
            if self.settings.ingest is None:
                common.append("forget")
        return common

    async def search(self, scene: str, query: str, limit: int) -> list[dict]:
        hits = await self.backend.search(scene, query, limit)
        if isinstance(self.backend, LocalMemory):
            return [{**asdict(hit), "score": None} for hit in hits]
        return [{"scope": hit.scope, "path": hit.path, "preview": hit.abstract,
                 "total_chars": None, "score": hit.score} for hit in hits]

    async def write(self, scene: str, path: str, content: str, reason: str, *,
                    scope: Scope = "scene") -> dict:
        if not reason.strip():
            raise ValueError("memory write reason must not be blank")
        async with self.write_lock("public" if scope == "public" else scene):
            if scene in self.pending_native_tasks:
                raise ValueError(f"OpenViking 抽取仍在处理：{self.pending_native_tasks[scene]}")
            if isinstance(self.backend, LocalMemory):
                result = (await self.backend.write(scene, path, content, reason) if scope == "scene"
                          else await self.backend.owner_write_public(path, content, reason))
            else:
                if scope != "scene":
                    raise ValueError("OpenViking 公共目录仅可读取")
                result = await self.backend.write(scene, path, content)
            return asdict(result)

    async def delete(self, scene: str, path: str, reason: str, *, forget: bool) -> dict:
        if not reason.strip():
            raise ValueError("memory delete reason must not be blank")
        if forget and self.settings.ingest is not None:
            raise ValueError("自动抽取的来源排除尚未接入；当前不能把删文件声称为遗忘，forget 暂不可用")
        async with self.write_lock(scene):
            if scene in self.pending_native_tasks:
                raise ValueError(f"OpenViking 抽取仍在处理：{self.pending_native_tasks[scene]}")
            if isinstance(self.backend, LocalMemory):
                result = (await self.backend.forget(scene, path) if forget
                          else await self.backend.delete(scene, path, reason))
            else:
                if forget:
                    raise ValueError("当前 OpenViking 接口未实现可访问历史和派生内容的完整遗忘")
                result = await self.backend.delete(scene, path)
            return asdict(result)

    async def history(self, scene: str, path: str) -> list[dict]:
        if not isinstance(self.backend, LocalMemory):
            raise ValueError("当前 OpenViking 接口不提供持久修改历史")
        return [asdict(change) for change in await self.backend.history(scene, path)]

    async def execute(self, scene: str, arguments: dict) -> str:
        item = MEMORY_ARGUMENTS.validate_python(arguments)
        if isinstance(item, BrowseMemory):
            result = asdict(await self.backend.browse(scene, item.path, scope=item.scope,
                                                     offset=item.offset, limit=item.limit))
        elif isinstance(item, ReadMemory):
            result = asdict(await self.backend.read(scene, item.path, scope=item.scope))
        elif isinstance(item, SearchMemory):
            result = {"hits": await self.search(scene, item.query, item.limit)}
        elif isinstance(item, WriteMemory):
            result = await self.write(scene, item.path, item.content, item.reason)
        elif isinstance(item, DeleteMemory):
            result = await self.delete(scene, item.path, item.reason, forget=item.action == "forget")
        else:
            result = {"changes": await self.history(scene, item.path)}
        return encode(result)

    async def recall(self, scene: str, messages: list[ChatMessage]) -> dict:
        """Recall from actual chat only; returned text never becomes a native history entry."""
        relevant = [message for message in messages if not message.is_self]
        queries = [plain_text(message).strip() for message in relevant]
        queries = [query for query in queries if query]
        budget = self.settings.recall_budget_chars
        items: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def append(scope: str, path: str, content: str, limit: int, *,
                   kind: str = "file", source_chars: int | None = None) -> None:
            nonlocal budget
            if (scope, path) in seen or budget <= 0:
                return
            shown = content[:min(budget, limit)]
            items.append({"scope": scope, "path": path, "content": shown, "kind": kind,
                          "truncated": len(shown) < len(content) or (
                              source_chars is not None and len(shown) < source_chars)})
            seen.add((scope, path))
            budget -= len(shown)

        if isinstance(self.backend, LocalMemory):
            qqs = list(dict.fromkeys(message.sender.uid for message in reversed(relevant)))[:4]
            profiles = await self.backend.profiles(scene, qqs)
            # Keep room for the latest topic; source and dates remain in the real file text.
            per_profile = min(900, budget // 2 // len(profiles)) if profiles else 0
            for profile in profiles:
                append("scene", profile.path, profile.content, per_profile)
        query = queries[-1][-1200:] if queries else ""
        if query and budget > 0:
            for hit in await self.search(scene, query, self.settings.recall_limit):
                append(hit["scope"], hit["path"], hit["preview"], budget,
                       kind="abstract" if hit["total_chars"] is None else "excerpt",
                       source_chars=hit["total_chars"])
        return {"backend": self.settings.backend, "query": query, "items": items,
                "content_chars": self.settings.recall_budget_chars - budget,
                "budget_chars": self.settings.recall_budget_chars}


@asynccontextmanager
async def open_memory(config: SharedConfig):
    settings = config.memory
    if settings is None:
        yield None
    elif isinstance(settings, OpenVikingMemoryConfig):
        async with OpenVikingMemory(settings.openviking) as backend:
            yield MemoryService(settings, backend)
    else:
        binding = settings.local.embedding
        if binding is None:
            yield MemoryService(settings, await asyncio.to_thread(LocalMemory, settings.local))
        else:
            provider = config.models.providers[binding.provider]
            resolved = EmbeddingSettings(**binding.model_dump(), base_url=provider.base_url,
                                         api_key=provider.api_key)
            async with EmbeddingClient(resolved) as embedding:
                backend = await asyncio.to_thread(LocalMemory, settings.local, embedding=embedding)
                yield MemoryService(settings, backend)
