"""Authenticated memory content management for configured host scenes."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import logging
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from ...configuration.types import STRICT
from ...runtime.network import NetworkRuntime
from ...chat.recall import RecallArguments, message_page
from ...runtime.logs import credentials, redact, redact_record
from ...memory.jobs import processing_records
from ...maintenance.commands import maintenance_command


logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    model_config = STRICT
    scene: str
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


class WriteRequest(BaseModel):
    model_config = STRICT
    scene: str
    path: str = Field(min_length=1)
    scope: Literal["scene", "public"]
    content: str
    reason: str = Field(min_length=1)


class PendingAdoption(BaseModel):
    model_config = STRICT
    scene: str
    source: str = Field(min_length=1)
    original: str
    target: str = Field(min_length=1)
    content: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    remove_source: bool


class SummaryRequest(BaseModel):
    model_config = STRICT
    scene: str
    path: str
    scope: Literal["scene", "public"]


class DeleteRequest(BaseModel):
    model_config = STRICT
    scene: str
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    forget: bool
    exclude_records: list[Annotated[int, Field(gt=0)]] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def matching_operation(self):
        if self.forget != (self.exclude_records is not None):
            raise ValueError("forget 必须显式选择 exclude_records（可为 []）；普通删除不接受来源排除")
        return self


def register_host_memory(app: FastAPI, *, runtime: NetworkRuntime, user, root, container: bool = False) -> None:
    secrets = credentials(runtime.config)

    def clean_embedding(value):
        return redact_record(value, lambda text: redact(text, secrets))

    @asynccontextmanager
    async def record_operation(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')
        try:
            with processing_records(runtime.config.database, None if runtime.memory is None else runtime.memory.jobs) as records:
                if records is None:
                    raise FileNotFoundError('本根未见现有记忆处理库；未创建记录源或启用服务，不能据此判断历史费用为零')
                yield records
        except Exception as error:
            logger.exception('记忆调用记录读取失败，场景 %s：%s', scene, error)
            code = 404 if isinstance(error, FileNotFoundError) else 422 if isinstance(error, ValueError) else 500
            raise HTTPException(code, redact(f'{type(error).__name__}: {error}', secrets)) from error

    @asynccontextmanager
    async def operation(scene: str):
        if scene not in runtime.chats:
            raise HTTPException(404, "当前宿主未配置这一场景")
        if runtime.memory is None:
            raise HTTPException(409, "当前运行配置未启用长期记忆后端")
        try:
            yield runtime.memory
        except Exception as error:
            logger.exception("记忆请求失败，场景 %s：%s", scene, error)
            code = (409 if isinstance(error, FileExistsError) else
                    404 if isinstance(error, FileNotFoundError) else
                    422 if isinstance(error, ValueError) else 500)
            raise HTTPException(code, redact(f"{type(error).__name__}: {error}", secrets)) from error

    @app.get("/api/host/memory/state")
    async def state(_: str = Depends(user)):
        memory = runtime.memory
        return {
            "enabled": memory is not None,
            "backend": None if memory is None else memory.settings.backend,
            "actions": [] if memory is None else memory.actions,
            "public_writable": memory is not None,
            "public_readable": memory is not None,
            "auto_recall": memory is not None and memory.settings.auto_recall,
            "recall_budget_chars": None if memory is None else memory.settings.recall_budget_chars,
            "summaries": memory is not None and memory.settings.summaries,
            "reindexing": memory is not None and memory.reindexing,
            "index": None if memory is None else await asyncio.to_thread(memory.backend.index_status),
            "reindex_command": maintenance_command(root, 'len_bot.next.maintenance.memory_reindex'),
            "maintenance_container": container,
            "scenes": [{"scene": scene, "persona": {"id": chat.persona.id, "name": chat.persona.name}}
                       for scene, chat in runtime.chats.items()],
            'persona_ids': {} if memory is None else {scene: sorted(memory.known_persona_ids(scene))
                                                     for scene in runtime.chats},
        }

    @app.post('/api/host/memory/reindex')
    async def reindex(_: str = Depends(user)):
        if runtime.memory is None:
            raise HTTPException(409, '当前运行配置未启用长期记忆后端')
        if runtime.stopped.is_set():
            raise HTTPException(409, '宿主正在停止')
        runtime.notify()
        try:
            return {'files': await runtime.memory.rebuild_index()}
        except Exception as error:
            logger.exception('记忆索引重建失败')
            raise HTTPException(409 if isinstance(error, FileExistsError) else 500,
                                redact(f'{type(error).__name__}: {error}', secrets)) from error
        finally:
            runtime.notify()

    @app.get("/api/host/memory/browse")
    async def browse(scene: str, path: str = "", scope: Literal["scene", "public"] = "scene",
                     offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                     _: str = Depends(user)):
        async with operation(scene) as memory:
            return asdict(await memory.backend.browse(scene, path, scope=scope, offset=offset, limit=limit))

    @app.get('/api/host/memory/embedding-calls')
    async def embedding_calls(scene: str, scope: Literal['scene', 'public'] = 'scene',
                              offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
                              snapshot: int | None = Query(default=None, ge=0), _: str = Depends(user)):
        async with record_operation(scene) as records:
            result = records.embedding_calls('public' if scope == 'public' else scene, offset=offset,
                                             limit=limit, snapshot=snapshot)
            return clean_embedding({**result, 'read_at': runtime.store.now()})

    @app.get('/api/host/memory/embedding-calls/{id}')
    async def embedding_call(id: int, scene: str, scope: Literal['scene', 'public'] = 'scene', _: str = Depends(user)):
        async with record_operation(scene) as records:
            record = records.embedding_call('public' if scope == 'public' else scene, id)
            return clean_embedding({'read_at': runtime.store.now(), 'call': record})

    @app.get("/api/host/memory/read")
    async def read(scene: str, path: str, scope: Literal["scene", "public"] = "scene", _: str = Depends(user)):
        async with operation(scene) as memory:
            return asdict(await memory.backend.read(scene, path, scope=scope))

    @app.get("/api/host/memory/history")
    async def history(scene: str, path: str, _: str = Depends(user)):
        async with operation(scene) as memory:
            return {"changes": await memory.history(scene, path)}


    @app.post("/api/host/memory/search")
    async def search(body: SearchRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return {"hits": await memory.search(body.scene, body.query, body.limit)}

    @app.put("/api/host/memory/file")
    async def write(body: WriteRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return await memory.write(body.scene, body.path, body.content, body.reason, scope=body.scope)

    @app.post('/api/host/memory/adopt-pending')
    async def adopt_pending(body: PendingAdoption, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return await memory.adopt_pending(body.scene, body.source, body.original, body.target,
                                              body.content, body.reason, remove_source=body.remove_source)

    @app.post("/api/host/memory/delete")
    async def delete(body: DeleteRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            return await memory.delete(body.scene, body.path, body.reason, forget=body.forget,
                                       exclude_records=body.exclude_records)


    @app.get("/api/host/memory/summary")
    async def summary(scene: str, path: str = "", scope: Literal["scene", "public"] = "scene",
                      _: str = Depends(user)):
        async with operation(scene) as memory:
            current = await memory.backend.summary(scene, path, scope=scope)
            return {"enabled": memory.summarizer is not None, "summary": asdict(current),
                    "runs": memory.jobs.summary_runs("public" if scope == "public" else scene, path)}

    @app.post("/api/host/memory/summary")
    async def summarize(body: SummaryRequest, _: str = Depends(user)):
        async with operation(body.scene) as memory:
            if memory.summarizer is None:
                raise ValueError("当前运行配置未开启目录摘要")
            async with memory.write_lock("public" if body.scope == "public" else body.scene):
                return await memory.summarizer.summarize(body.scene, body.path, scope=body.scope)

    @app.get("/api/host/memory/summary-runs/{id}")
    async def summary_run(id: int, scene: str, _: str = Depends(user)):
        async with operation(scene) as memory:
            run = memory.jobs.summary_run(id)
            if run is None or run["scene"] != scene:
                raise FileNotFoundError(f"当前场景没有目录摘要记录 {id}")
            return run

    @app.get("/api/host/memory/sources")
    async def sources(scene: str, query: str | None = None, who: str | None = None,
                      snapshot: int | None = Query(default=None, ge=0), offset: int = Query(default=0, ge=0),
                      _: str = Depends(user)):
        async with operation(scene) as memory:
            arguments = RecallArguments(query=query, who=who, snapshot=snapshot, offset=offset)
            current = runtime.store.max_message_seq(scene)
            boundary = current if arguments.snapshot is None else arguments.snapshot
            if boundary > current:
                raise ValueError("snapshot 超过当前场景消息末尾")
            rows = runtime.store.search_messages(scene, query=arguments.query, who=arguments.who,
                                                after=None, before=None, snapshot=boundary,
                                                offset=arguments.offset, limit=11)
            excluded = set(memory.jobs.excluded_records(scene))
            return {"snapshot": boundary, "offset": arguments.offset,
                    "next_offset": arguments.offset + 10 if len(rows) > 10 else None,
                    "previews": [{**message_page(seq, message, runtime.config.scene_timezone(scene), offset=0, size=500),
                                  "excluded": seq in excluded} for seq, message in rows[:10]]}

    @app.get("/api/host/memory/ingest")
    async def ingest_state(_: str = Depends(user)):
        ingestor = runtime.ingestor
        return {"enabled": ingestor is not None, "scenes": [] if ingestor is None else [
            {**ingestor.jobs.view(scene), "worker_error": (
                f"{type(ingestor.errors[scene]).__name__}: {ingestor.errors[scene]}"
                if scene in ingestor.errors else None)} for scene in runtime.chats
        ]}

    @app.post("/api/host/memory/ingest/{scene}/{action}")
    async def ingest(scene: str, action: Literal["run", "retry"], _: str = Depends(user)):
        async with operation(scene):
            ingestor = runtime.ingestor
            if ingestor is None:
                raise ValueError("当前运行配置未开启记忆自动抽取")
            if action == "retry":
                return {"requested": "retry", "job": ingestor.retry(scene)}
            ingestor.request(scene)
            return {"requested": action, "state": ingestor.jobs.view(scene)}
