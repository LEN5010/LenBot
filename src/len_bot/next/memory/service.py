"""Local memory, scene-bound tools and transient per-turn recall."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
from dataclasses import asdict
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .embeddings import EmbeddingClient, EmbeddingSettings
from .local import LocalMemory, LocalMemorySettings
from .jobs import MemoryJobs
from .summary import MemorySummarizer
from .role_paths import require_bot_path
from ..platform.messages import ChatMessage, plain_text, render_message
from ..models.client import ChatModel
from ..models.slots import ModelSlots
from ..storage.store import Store, encode
from ..models.tokens import token_record

if TYPE_CHECKING:
    from ..config import SharedConfig


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


MemorySettings = LocalMemoryConfig

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
    "普通删除保留修改历史；forget 清除该文件及其历史，不能据此宣称聊天和备份已删除。",
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
    def __init__(self, settings: LocalMemoryConfig,
                 backend: LocalMemory, *, jobs: MemoryJobs, store: Store,
                 active_personas: dict[str, str]):
        self.settings, self.backend = settings, backend
        self.jobs, self.store = jobs, store
        self.active_personas = dict(active_personas)
        for scene in self.store.message_persona_scenes() | self.active_personas.keys():
            ids = self.store.message_persona_ids(scene)
            if scene in self.active_personas:
                ids.add(self.active_personas[scene])
            self.jobs.remember_personas(scene, ids)
        self.summarizer: MemorySummarizer | None = None
        # A complete read/generate/write extraction shares this queue with edits.
        self.write_locks: dict[str, asyncio.Lock] = {}

    async def read_group_profile(self, scene: str) -> str | None:
        """Read the current derived profile under the backend's existing write/read lock."""
        if self.summarizer is None:
            return None
        summary = await self.backend.summary(scene)
        return summary.overview if summary.changed_after is None else None

    def write_lock(self, scene: str) -> asyncio.Lock:
        return self.write_locks.setdefault(scene, asyncio.Lock())

    def known_persona_ids(self, scene: str) -> set[str]:
        return self.jobs.persona_ids(scene)

    @property
    def actions(self) -> list[str]:
        return ["browse", "read", "search", "write", "delete", "history", "forget"]

    async def search(self, scene: str, query: str, limit: int, *, automatic: bool = False) -> list[dict]:
        hits = await self.backend.search(scene, query, limit, exclude_pending=automatic, automatic=automatic)
        return [{**asdict(hit), "score": None} for hit in hits]

    async def write(self, scene: str, path: str, content: str, reason: str, *,
                    scope: Scope = "scene") -> dict:
        if not reason.strip():
            raise ValueError("memory write reason must not be blank")
        async with self.write_lock("public" if scope == "public" else scene):
            require_bot_path(path, self.known_persona_ids(scene))
            result = (await self.backend.write(scene, path, content, reason) if scope == "scene"
                      else await self.backend.owner_write_public(path, content, reason))
            return asdict(result)

    async def delete(self, scene: str, path: str, reason: str, *, forget: bool,
                     exclude_records: list[int] | None = None) -> dict:
        if not reason.strip():
            raise ValueError("memory delete reason must not be blank")
        if forget != (exclude_records is not None):
            raise ValueError("forget 必须显式选择 exclude_records（可为 []）；普通 delete 不接受来源排除")
        async with self.write_lock(scene):
            if forget:
                selected = sorted(set(exclude_records))
                self.store.check_message_records(scene, selected)
                added = self.jobs.exclude_records(scene, selected)
                try:
                    result = await self.backend.forget(scene, path)
                except Exception as error:
                    raise RuntimeError(
                        f"已保存 {len(selected)} 条原话的抽取排除；记忆遗忘未完成：{type(error).__name__}: {error}"
                    ) from error
                return {**asdict(result), "excluded_records": selected, "new_exclusions": added}
            result = await self.backend.delete(scene, path, reason)
            return asdict(result)

    async def adopt_pending(self, scene: str, source: str, original: str, target: str,
                            content: str, reason: str, *, remove_source: bool) -> dict:
        """One explicit owner operation, not a model tool or a multi-file transaction."""
        if not source.startswith(LEGACY_IMPORT + '/'):
            raise ValueError(f'采用源必须是本场景实际待确认原件：{source!r}')
        if target == LEGACY_IMPORT or target.startswith(LEGACY_IMPORT + '/'):
            raise ValueError('正式目标不能继续位于待确认区域')
        if not reason.strip() or not content.strip():
            raise ValueError('采用须明确核对后的非空正文与实际原因')
        async with self.write_lock(scene):
            saved = await self.backend.read(scene, source, scope='scene')
            if saved.content != original:
                raise ValueError(f'待确认原件已变化，本次未创建正式文件：{source!r}。请重读原文后明确核对。')
            require_bot_path(target, self.known_persona_ids(scene))
            change = await self.backend.write(scene, target, content, reason, create_only=True)
            if remove_source:
                try:
                    await self.backend.delete(scene, source, reason)
                except Exception as error:
                    raise RuntimeError(f'正式文件{target!r}已保存，但待确认原件{source!r}删除失败：'
                                       f'{type(error).__name__}: {error}。请核对两边，不自动重试。') from error
            return {'source': source, 'target': target, 'target_saved': True, 'source_removed': remove_source,
                    'change': asdict(change), 'notice': '仅本场景正式正文采用；源普通删除仍留历史，不是完整遗忘。'
                    '原聊天、抽取位置/排除与角色样例未改，无公共提升。'}

    async def history(self, scene: str, path: str) -> list[dict]:
        return [asdict(change) for change in await self.backend.history(scene, path)]

    async def execute(self, scene: str, arguments: dict) -> str:
        try:
            item = MEMORY_ARGUMENTS.validate_python(arguments)
        except ValidationError as error:
            raise ValueError(f"Invalid memory arguments: {repr(arguments)[:500]}; {error}") from error
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
        # Semantic retrieval keeps speakers; literal retrieval must not match timestamps/accounts.
        text_only = self.backend.embedding is None
        # Short replies still need the preceding topic, including our question.
        queries = [plain_text(message) if text_only else render_message(message, timezone="UTC") for message in messages
                   if plain_text(message).strip() and message.send_status in {"received", "sent", "simulated"}]
        budget = self.settings.recall_budget_chars
        items: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def append(scope: str, path: str, content: str, limit: int, *,
                   kind: str = "file", source_chars: int | None = None) -> None:
            nonlocal budget
            if not content.strip() or limit == 0 or (scope, path) in seen or budget <= 0:
                return
            shown = content[:min(budget, limit)]
            items.append({"scope": scope, "path": path, "content": shown, "kind": kind,
                          "truncated": len(shown) < len(content) or (
                              source_chars is not None and len(shown) < source_chars)})
            seen.add((scope, path))
            budget -= len(shown)

        users = list(dict.fromkeys(message.sender.uid for message in reversed(relevant)))[:4]
        profiles = await self.backend.profiles(scene, users)
        # Keep room for the latest topic; source and dates remain in the real file text.
        per_profile = min(900, budget // 2 // len(profiles)) if profiles else 0
        for profile in profiles:
            append("scene", profile.path, profile.content, per_profile)
        query = "\n".join(queries)[-1200:]
        if query and budget > 0:
            for hit in await self.search(scene, query, self.settings.recall_limit, automatic=True):
                append(hit["scope"], hit["path"], hit["preview"], budget,
                       kind="excerpt",
                       source_chars=hit["total_chars"])
        return {"backend": self.settings.backend, "query": query, "items": items,
                "content_chars": self.settings.recall_budget_chars - budget,
                "budget_chars": self.settings.recall_budget_chars}


@asynccontextmanager
async def open_memory_backend(config: SharedConfig):
    settings = config.memory
    if settings is None:
        yield None
    else:
        binding = settings.local.embedding
        if binding is None:
            yield await asyncio.to_thread(LocalMemory, settings.local)
        else:
            provider = config.models.providers[binding.provider]
            resolved = EmbeddingSettings(**binding.model_dump(), base_url=provider.base_url,
                                         api_key=provider.api_key, proxy=provider.proxy)
            async with EmbeddingClient(resolved) as embedding:
                backend = await asyncio.to_thread(LocalMemory, settings.local, embedding=embedding)
                yield backend


@asynccontextmanager
async def open_memory(config: SharedConfig, store: Store, *, active_personas: dict[str, str] | None = None,
                      slots: ModelSlots | None = None):
    async with open_memory_backend(config) as backend:
        if backend is None:
            yield None
            return
        with MemoryJobs(config.database.with_name(config.database.name + ".memory.sqlite3")) as jobs:
            jobs.recover_embeddings(store.now())
            # Offline index/import owners do not have a running chat role.
            service = MemoryService(config.memory, backend, jobs=jobs, store=store,
                                    active_personas={} if active_personas is None else active_personas)
            if backend.embedding is not None:
                binding = config.memory.local.embedding
                async def embed(source, texts, purpose):
                    async with (slots.slot(scene=source) if slots is not None else nullcontext()):
                        with jobs.db:
                            call_id = jobs.db.execute(
                                "INSERT INTO memory_embedding_calls(scene,purpose,started,request) VALUES(?,?,?,?)",
                                (source, purpose, store.now(), encode({"texts": texts,
                                 "settings": backend.embedding.settings.model_dump(mode="json", exclude={"api_key"})})),
                            ).lastrowid
                        try:
                            batch = await backend.embedding.embed(texts)
                        except BaseException as error:
                            with jobs.db:
                                jobs.db.execute("UPDATE memory_embedding_calls SET ended=?,error=? WHERE id=?",
                                                (store.now(), f"{type(error).__name__}: {error}", call_id))
                            raise
                        with jobs.db:
                            tokens = token_record(batch.token_usage)
                            jobs.db.execute("UPDATE memory_embedding_calls SET ended=?,response=?,usage=?,tokens=? WHERE id=?",
                                            (store.now(), encode({"vector_count":len(batch.vectors),"dimensions":batch.dimensions}),
                                             None if batch.usage is None else encode(batch.usage),
                                             None if tokens is None else encode(tokens), call_id))
                        return batch
                backend.track_embedding = embed

            if not config.memory.summaries:
                yield service
                return
            async with ChatModel(config.model_settings("memory")) as model:
                service.summarizer = MemorySummarizer(config, backend, jobs, model, slots=slots)
                yield service
