"""Selected memory backend, scene-bound tools and transient per-turn recall."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from string import Template
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .memory_embeddings import EmbeddingClient, EmbeddingSettings
from .memory_local import LocalMemory, LocalMemorySettings
from .memory_jobs import MemoryJobs
from .memory_openviking import OpenVikingMemory, OpenVikingSettings
from .memory_summary import MemorySummarizer
from .messages import ChatMessage, plain_text
from .model import ChatModel
from .model_slots import ModelSlots
from .store import Store, encode
from .pricing import estimate_cost

if TYPE_CHECKING:
    from .config import SharedConfig


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
Scope = Literal["scene", "public"]
# Offline-imported legacy memories awaiting operator confirmation (M11 migration).
LEGACY_IMPORT = "legacy-import"


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
    # Derived directory abstracts/overviews generated with models.roles.memory.
    summaries: bool = False


class OpenVikingMemoryConfig(RecallSettings):
    backend: Literal["openviking"]
    openviking: OpenVikingSettings
    summaries: bool = False


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
    action: Literal["delete"]
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ForgetMemory(BaseModel):
    model_config = STRICT
    action: Literal["forget"]
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    exclude_records: list[Annotated[int, Field(gt=0)]] = Field(max_length=500)


class HistoryMemory(BaseModel):
    model_config = STRICT
    action: Literal["history"]
    path: str = Field(min_length=1)


MEMORY_ARGUMENTS = TypeAdapter(Annotated[
    BrowseMemory | ReadMemory | SearchMemory | WriteMemory | DeleteMemory | ForgetMemory | HistoryMemory,
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
            "exclude_records": {"type": "array", "items": {"type": "integer", "minimum": 1},
                                "maxItems": 500, "description": "仅 forget 必填：选中的 recall_chat 原话 record 此后不再后台抽取。"
                                "显式 [] 表示不排除旧输入；不扩大到未选择的消息或未来再次讲述。"},
            "offset": {"type": "integer", "minimum": 0, "description": "仅 browse 的分页起点，默认 0。"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                      "description": "browse 默认 20、最多 100；search 默认 5、最多 20。"},
        },
    },
}}


class MemoryService:
    def __init__(self, settings: LocalMemoryConfig | OpenVikingMemoryConfig,
                 backend: LocalMemory | OpenVikingMemory, *, jobs: MemoryJobs, store: Store):
        self.settings, self.backend = settings, backend
        self.jobs, self.store = jobs, store
        self.summarizer: MemorySummarizer | None = None
        # A complete read/generate/write extraction shares this queue with edits.
        self.write_locks: dict[str, asyncio.Lock] = {}
        self.pending_native_tasks: dict[str, str] = {}
        if isinstance(settings, OpenVikingMemoryConfig):
            for scene in settings.openviking.scenes:
                latest = jobs.latest(scene)
                if latest is None or latest["backend"] != "openviking":
                    continue
                task_id = latest["details"].get("task_id")
                if latest["status"] == "submitted" and task_id is not None:
                    self.pending_native_tasks[scene] = task_id
                elif latest["details"].get("native_phase") == "submitting" and task_id is None:
                    self.pending_native_tasks[scene] = "submission outcome unknown"

    def group_profile(self, scene: str) -> str | None:
        """Scene root overview for the system text; only local summaries provide one."""
        if self.summarizer is None:
            return None
        return self.backend.summary_text_sync(scene)

    async def read_group_profile(self, scene: str) -> str | None:
        """Read the current derived profile under the backend's existing write/read lock."""
        if isinstance(self.backend, OpenVikingMemory):
            if not self.settings.summaries:
                return None
            async with self.write_lock(scene):
                if scene in self.pending_native_tasks:
                    return None
                overviews = [await self.backend.overview(scene, path)
                             for path in await self.backend.memory_directories(scene)]
            prompt_root = Path(__file__).resolve().parents[1] / "prompts"
            section = Template((prompt_root / "next_native_memory_section.md").read_text())
            included: list[str] = []
            omitted: list[str] = []
            for current in overviews:
                freshness = current.freshness
                if (freshness is None or freshness.pending_child_changes or freshness.unsampled_entries
                        or freshness.missing_summary_entries):
                    omitted.append(encode({"path": current.path, "freshness":
                                          None if freshness is None else freshness.model_dump()}))
                    continue
                included.append(section.substitute(path=current.path, overview=current.content,
                    missing="未报告" if freshness.missing_summary_entries is None
                    else freshness.missing_summary_entries))
            if not included:
                return None
            return Template((prompt_root / "next_native_memory_overview.md").read_text()).substitute(
                included="\n\n".join(included), omitted="\n".join(omitted) if omitted else "无")
        if self.summarizer is None:
            return None
        summary = await self.backend.summary(scene)
        return summary.overview if summary.changed_after is None else None

    def write_lock(self, scene: str) -> asyncio.Lock:
        return self.write_locks.setdefault(scene, asyncio.Lock())

    @property
    def actions(self) -> list[str]:
        common = ["browse", "read", "search", "write", "delete", "history"]
        if isinstance(self.backend, LocalMemory):
            common.append("forget")
        return common

    async def search(self, scene: str, query: str, limit: int, *, automatic: bool = False) -> list[dict]:
        if isinstance(self.backend, LocalMemory):
            hits = await self.backend.search(scene, query, limit, exclude_pending=automatic)
            return [{**asdict(hit), "score": None} for hit in hits]
        hits = await self.backend.search(scene, query, limit)
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

    async def delete(self, scene: str, path: str, reason: str, *, forget: bool,
                     exclude_records: list[int] | None = None) -> dict:
        if not reason.strip():
            raise ValueError("memory delete reason must not be blank")
        if forget != (exclude_records is not None):
            raise ValueError("forget 必须显式选择 exclude_records（可为 []）；普通 delete 不接受来源排除")
        async with self.write_lock(scene):
            if scene in self.pending_native_tasks:
                raise ValueError(f"OpenViking 抽取仍在处理：{self.pending_native_tasks[scene]}")
            if isinstance(self.backend, LocalMemory):
                if forget:
                    selected = sorted(set(exclude_records))
                    self.store.check_message_records(scene, selected)
                    added = self.jobs.exclude_records(scene, selected)
                    try:
                        result = await self.backend.forget(scene, path)
                    except Exception as error:
                        raise RuntimeError(
                            f"已保存 {len(selected)} 条原话的抽取排除；记忆删除失败：{type(error).__name__}: {error}"
                        ) from error
                    return {**asdict(result), "excluded_records": selected, "new_exclusions": added}
                result = await self.backend.delete(scene, path, reason)
            else:
                if forget:
                    raise ValueError("当前 OpenViking 接口未实现可访问历史和派生内容的完整遗忘")
                result = await self.backend.delete(scene, path)
            return asdict(result)

    async def history(self, scene: str, path: str) -> list[dict]:
        if not isinstance(self.backend, LocalMemory):
            return await self.backend.history(scene, path)
        return [asdict(change) for change in await self.backend.history(scene, path)]

    async def execute(self, scene: str, arguments: dict) -> str:
        try:
            item = MEMORY_ARGUMENTS.validate_python(arguments)
        except ValidationError as error:
            raise ValueError(f"Invalid memory arguments: {repr(arguments)[:500]}; {error}") from error
        if item.action not in self.actions:
            raise ValueError(f"当前 {self.settings.backend} 记忆后端不支持 {item.action}")
        if isinstance(item, BrowseMemory):
            result = asdict(await self.backend.browse(scene, item.path, scope=item.scope,
                                                     offset=item.offset, limit=item.limit))
        elif isinstance(item, ReadMemory):
            result = asdict(await self.backend.read(scene, item.path, scope=item.scope))
        elif isinstance(item, SearchMemory):
            result = {"hits": await self.search(scene, item.query, item.limit)}
        elif isinstance(item, WriteMemory):
            result = await self.write(scene, item.path, item.content, item.reason)
        elif isinstance(item, (DeleteMemory, ForgetMemory)):
            result = await self.delete(scene, item.path, item.reason, forget=isinstance(item, ForgetMemory),
                                       exclude_records=item.exclude_records if isinstance(item, ForgetMemory) else None)
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
            for hit in await self.search(scene, query, self.settings.recall_limit, automatic=True):
                append(hit["scope"], hit["path"], hit["preview"], budget,
                       kind="abstract" if hit["total_chars"] is None else "excerpt",
                       source_chars=hit["total_chars"])
        return {"backend": self.settings.backend, "query": query, "items": items,
                "content_chars": self.settings.recall_budget_chars - budget,
                "budget_chars": self.settings.recall_budget_chars}


@asynccontextmanager
async def open_memory_backend(config: SharedConfig):
    settings = config.memory
    if settings is None:
        yield None
    elif isinstance(settings, OpenVikingMemoryConfig):
        async with OpenVikingMemory(settings.openviking) as backend:
            yield backend
    else:
        binding = settings.local.embedding
        if binding is None:
            yield await asyncio.to_thread(LocalMemory, settings.local)
        else:
            provider = config.models.providers[binding.provider]
            resolved = EmbeddingSettings(**binding.model_dump(), base_url=provider.base_url,
                                         api_key=provider.api_key)
            async with EmbeddingClient(resolved) as embedding:
                backend = await asyncio.to_thread(LocalMemory, settings.local, embedding=embedding)
                yield backend


@asynccontextmanager
async def open_memory(config: SharedConfig, store: Store, *, slots: ModelSlots | None = None):
    async with open_memory_backend(config) as backend:
        if backend is None:
            yield None
            return
        with MemoryJobs(config.database.with_name(config.database.name + ".memory.sqlite3")) as jobs:
            service = MemoryService(config.memory, backend, jobs=jobs, store=store)
            if isinstance(backend, LocalMemory) and backend.embedding is not None:
                binding = config.memory.local.embedding
                price = config.models.prices.get(binding.provider, {}).get(binding.model)
                async def embed(source, texts, purpose):
                    async with (slots.slot(scene=source) if slots is not None else nullcontext()):
                        with jobs.db:
                            call_id = jobs.db.execute(
                                "INSERT INTO memory_embedding_calls(scene,purpose,started,request) VALUES(?,?,?,?)",
                                (source, purpose, store.now(), encode({"texts": texts,
                                 "settings": backend.embedding.settings.model_dump(mode="json", exclude={"api_key"}),
                                 "price": None if price is None else price.model_dump(mode="json")})),
                            ).lastrowid
                        try:
                            batch = await backend.embedding.embed(texts)
                        except BaseException as error:
                            with jobs.db:
                                jobs.db.execute("UPDATE memory_embedding_calls SET ended=?,error=? WHERE id=?",
                                                (store.now(), f"{type(error).__name__}: {error}", call_id))
                            raise
                        with jobs.db:
                            cost = estimate_cost(price, batch.token_usage)
                            jobs.db.execute("UPDATE memory_embedding_calls SET ended=?,response=?,usage=?,cost=? WHERE id=?",
                                            (store.now(), encode({"vector_count":len(batch.vectors),"dimensions":batch.dimensions}),
                                             None if batch.usage is None else encode(batch.usage),
                                             None if cost is None else encode(cost), call_id))
                        return batch
                backend.track_embedding = embed

            if not (isinstance(config.memory, LocalMemoryConfig) and config.memory.summaries):
                yield service
                return
            async with ChatModel(config.model_settings("memory")) as model:
                service.summarizer = MemorySummarizer(config, backend, jobs, model, slots=slots)
                yield service
