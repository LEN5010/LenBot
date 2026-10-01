"""OpenViking native memory view, partitioned by the existing QQ scene identity.

This is an HTTP boundary, not the complete M11 MemoryBackend: it does not
provide per-write history or irreversible forgetting; snapshot history is native.
Public writes are available only to explicit offline transfer, not runtime tools.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .messages import ChatMessage, render_message
from .memory_types import MemoryDocument, MemoryNode, MemoryPage
from .memory_overview import NativeOverview, OverviewRefresh, parse_overview, parse_refresh
from .replay_memory import RecordedMemory, RecordedMemoryClient


_SCENE = re.compile(r"(?:group|private):[1-9][0-9]*\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.@-]+\Z")
_QQ = re.compile(r"[1-9][0-9]*\Z")
_PUBLIC_PREFIX = "viking://resources/"
_DERIVED_FILES = frozenset({".abstract.md", ".overview.md"})


class SceneIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    user_id: str
    api_key: str = Field(repr=False)

    @field_validator("user_id")
    @classmethod
    def valid_user_id(cls, value: str) -> str:
        if value in {".", ".."} or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("user_id must be one OpenViking user path segment")
        return value

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("api_key must be nonblank without line breaks")
        return value


class NativeMemoryTarget(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool


class NativeMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, serialize_by_alias=True)
    self_target: NativeMemoryTarget = Field(alias='self')
    peer: NativeMemoryTarget
    working_memory: NativeMemoryTarget = Field(default_factory=lambda: NativeMemoryTarget(enabled=True))
    memory_types: list[str] | None = None

    @field_validator('memory_types')
    @classmethod
    def explicit_types(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if any(not name.strip() for name in value) or len(value) != len(set(value)):
                raise ValueError('memory_policy.memory_types must be distinct nonblank native category names')
        return value

    def wire(self) -> dict:
        result = self.model_dump(mode='json', by_alias=True)
        if self.memory_types is not None:
            result['memory_types'] = sorted(self.memory_types)
        return result


class OpenVikingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    base_url: str
    account_id: str
    scenes: dict[str, SceneIdentity] = Field(min_length=1)
    timeout_seconds: float = Field(default=20, gt=0, allow_inf_nan=False)
    public_root: str | None = None
    memory_policy: NativeMemoryPolicy | None = None

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or value.endswith("/api/v1")):
            raise ValueError("base_url must be the OpenViking HTTP origin or mount, without credentials/query/fragment/api prefix")
        return value.rstrip("/")

    @field_validator("account_id")
    @classmethod
    def valid_account_id(cls, value: str) -> str:
        if value in {".", ".."} or value.startswith("_") or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError("account_id must be one OpenViking account path segment")
        return value

    @field_validator("public_root")
    @classmethod
    def valid_public_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(_PUBLIC_PREFIX):
            raise ValueError("public_root must be beneath viking://resources/")
        _segments(value[len(_PUBLIC_PREFIX):])
        return value.rstrip("/")

    @model_validator(mode="after")
    def valid_scenes(self) -> OpenVikingSettings:
        if any(_SCENE.fullmatch(scene) is None for scene in self.scenes):
            raise ValueError("scenes keys must be existing group:<QQ> or private:<QQ> identities")
        users = [identity.user_id for identity in self.scenes.values()]
        if len(users) != len(set(users)):
            raise ValueError("each scene must have a distinct OpenViking user_id")
        return self


@dataclass(frozen=True, slots=True)
class MemoryHit:
    scope: Literal["scene", "public"]
    path: str
    score: float
    abstract: str


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    path: str
    content_updated: bool
    overview_status: str | None
    semantic_status: str
    vector_status: str


@dataclass(frozen=True, slots=True)
class MemoryDelete:
    path: str
    semantic_status: str | None
    estimated_deleted_count: int | None


@dataclass(frozen=True, slots=True)
class IngestReceipt:
    status: Literal["accepted", "skipped"]
    session_id: str
    task_id: str | None
    archive_uri: str | None
    memory_policy: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class IngestTask:
    task_id: str
    status: Literal["pending", "running", "cancelling", "completed", "failed", "cancelled"]
    error: str | None
    memories_extracted: dict[str, int] | None
    memories_extracted_total: int | None
    result: dict[str, Any] | None


class _Node(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    name: str
    uri: str
    isDir: bool
    access: str | None = None


class _Hit(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    uri: str
    context_type: Literal["memory", "resource", "skill"]
    level: int
    score: float = Field(allow_inf_nan=False)
    abstract: str


class _Find(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    memories: list[_Hit]
    resources: list[_Hit]
    skills: list[_Hit]
    total: int


class _Write(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    uri: str
    context_type: Literal["memory", "resource", "skill"]
    content_updated: bool
    semantic_status: str
    vector_status: str
    overview_status: str


class _Delete(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    uri: str
    semantic_status: str | None = None
    estimated_deleted_count: int | None = None


class _Commit(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    session_id: str
    status: Literal["accepted", "skipped"]
    task_id: str | None
    archive_uri: str | None
    archived: bool


class _Task(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    task_id: str
    task_type: str
    resource_id: str | None
    status: Literal["pending", "running", "cancelling", "completed", "failed", "cancelled"]
    error: str | None
    result: dict[str, Any] | None


def _segments(path: str) -> tuple[str, ...]:
    if not path or path.startswith("/") or path.endswith("/"):
        raise ValueError(f"memory path must be a nonempty relative path: {path!r}")
    parts = tuple(path.split("/"))
    if any(not part or part in {".", ".."} or part in _DERIVED_FILES
           or any(ord(character) < 32 for character in part)
           or any(character in part for character in "\\?#%") for part in parts):
        raise ValueError(f"memory path has an unsupported segment: {path!r}")
    return parts


def _scene_path(path: str, *, file: bool = False) -> str:
    parts = _segments(path)
    if parts[0] == "memories":
        allowed = True
    elif parts[0] == "peers":
        allowed = (len(parts) == 1 or
                   (_QQ.fullmatch(parts[1]) is not None and
                    (len(parts) == 2 or parts[2] == "memories")))
    else:
        allowed = False
    if not allowed or (file and (len(parts) < 2 or parts[-1] in {"memories", "peers"}
                            or not parts[-1].endswith(".md"))):
        raise ValueError(f"memory path must stay in native scene memories: {path!r}")
    if file and parts[0] == "peers" and (len(parts) < 4 or parts[2] != "memories"):
        raise ValueError(f"peer memory file must be beneath peers/<QQ>/memories/: {path!r}")
    return "/".join(parts)


def _as(model: type[BaseModel], value: object, raw: str) -> Any:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ValueError(f"OpenViking invalid {model.__name__} response: {error}; raw={raw[:500]!r}") from error


def parse_ingest_task(value: object, *, task_id: str, raw: str) -> IngestTask:
    """Parse the native session-commit result, whose extraction counts are per category."""
    task = _as(_Task, value, raw)
    if task.task_id != task_id or task.task_type != "session_commit":
        raise ValueError(f"OpenViking task is not the requested session commit; raw={raw[:500]!r}")
    if task.status == "failed" and not task.error:
        raise ValueError(f"OpenViking failed task has no original error; raw={raw[:500]!r}")
    counts: dict[str, int] | None = None
    if task.status == "completed" and task.result is not None and "memories_extracted" in task.result:
        counts = task.result["memories_extracted"]
        if not isinstance(counts, dict) or any(
            not isinstance(category, str) or type(count) is not int or count < 0
            for category, count in counts.items()
        ):
            raise ValueError(f"OpenViking completed task has invalid extraction counts; raw={raw[:500]!r}")
    return IngestTask(task_id=task_id, status=task.status, error=task.error,
                      memories_extracted=counts,
                      memories_extracted_total=None if counts is None else sum(counts.values()),
                      result=task.result)


class _SnapshotEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    oid: str = Field(min_length=1)
    message: str
    parents: list[str]


class _SnapshotDiff(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    path: str
    from_commit: str
    to_commit: str
    change_type: str
    diff_text: str


class OpenVikingMemory:
    def __init__(self, settings: OpenVikingSettings, *, recordings: Path | None = None):
        self.settings = settings
        self._client = (
            RecordedMemoryClient(RecordedMemory(recordings), settings)
            if recordings is not None else httpx.AsyncClient(
                timeout=settings.timeout_seconds, trust_env=False, follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            )
        )
        self._verified_scenes: set[str] = set()
        self._identity_locks = {scene: asyncio.Lock() for scene in settings.scenes}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OpenVikingMemory:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _scene(self, scene: str) -> SceneIdentity:
        try:
            return self.settings.scenes[scene]
        except KeyError as error:
            raise ValueError(f"OpenViking has no configured identity for scene {scene!r}") from error

    async def _request(self, identity: SceneIdentity, method: str, endpoint: str, *,
                       params: dict[str, object] | None = None,
                       body: dict[str, object] | None = None) -> tuple[dict[str, Any], str]:
        url = f"{self.settings.base_url}{endpoint}"
        deadline = asyncio.timeout(self.settings.timeout_seconds)
        try:
            async with deadline:
                response = await self._client.request(
                    method, url, params=params, json=body,
                    headers={"X-API-Key": identity.api_key},
                )
        except TimeoutError as error:
            if deadline.expired():
                raise TimeoutError(f"OpenViking {method} {endpoint} exceeded {self.settings.timeout_seconds} seconds") from error
            raise
        raw = response.text
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise ValueError(f"OpenViking {method} {endpoint} HTTP {response.status_code} invalid JSON: "
                             f"{error}; raw={raw[:500]!r}") from error
        if not isinstance(payload, dict):
            raise ValueError(f"OpenViking {method} {endpoint} expected object; raw={raw[:500]!r}")
        if response.status_code != 200 or payload.get("status") != "ok":
            raise ValueError(f"OpenViking {method} {endpoint} HTTP {response.status_code}: {raw[:2000]}")
        if "result" not in payload:
            raise ValueError(f"OpenViking {method} {endpoint} missing result; raw={raw[:500]!r}")
        return payload, raw

    async def _identity(self, scene: str) -> SceneIdentity:
        identity = self._scene(scene)
        if scene in self._verified_scenes:
            return identity
        async with self._identity_locks[scene]:
            if scene in self._verified_scenes:
                return identity
            deadline = asyncio.timeout(self.settings.timeout_seconds)
            try:
                async with deadline:
                    response = await self._client.get(
                        f"{self.settings.base_url}/health",
                        headers={"X-API-Key": identity.api_key},
                    )
            except TimeoutError as error:
                if deadline.expired():
                    raise TimeoutError(f"OpenViking identity check exceeded {self.settings.timeout_seconds} seconds") from error
                raise
            raw = response.text
            try:
                payload = json.loads(raw)
            except ValueError as error:
                raise ValueError(f"OpenViking /health HTTP {response.status_code} invalid JSON: "
                                 f"{error}; raw={raw[:500]!r}") from error
            if (response.status_code != 200 or not isinstance(payload, dict)
                    or payload.get("status") != "ok" or payload.get("role") != "user"
                    or payload.get("account_id") != self.settings.account_id
                    or payload.get("user_id") != identity.user_id):
                raise ValueError(f"OpenViking scene {scene!r} identity does not match configured account/user role; "
                                 f"HTTP {response.status_code}; raw={raw[:500]!r}")
            self._verified_scenes.add(scene)
        return identity

    def _uri(self, identity: SceneIdentity, path: str, scope: Literal["scene", "public"], *,
             file: bool = False) -> str:
        if scope == "scene":
            return f"viking://user/{identity.user_id}/{_scene_path(path, file=file)}"
        if scope == "public" and self.settings.public_root is not None:
            if file and not path:
                raise ValueError("public memory file path is required")
            suffix = "/".join(_segments(path)) if path else ""
            return self.settings.public_root + (f"/{suffix}" if suffix else "")
        raise ValueError("public memory root is not configured")

    def _path_from_uri(self, identity: SceneIdentity, uri: str,
                       scope: Literal["scene", "public"]) -> str:
        root = (f"viking://user/{identity.user_id}" if scope == "scene"
                else self.settings.public_root)
        if root is None or not uri.startswith(root + "/"):
            raise ValueError(f"OpenViking returned URI outside {scope} source: {uri!r}")
        path = uri[len(root) + 1:].rstrip("/")
        if scope == "scene":
            return _scene_path(path)
        _segments(path)
        return path

    async def _list_directory(self, identity: SceneIdentity, uri: str, *, offset: int,
                              limit: int, show_hidden: bool = False) -> tuple[list[_Node], bool]:
        params = {
            "uri": uri, "output": "original", "offset": offset, "limit": limit, "sort_by": "name",
        }
        if show_hidden:
            params["show_all_hidden"] = True
        payload, raw = await self._request(identity, "GET", "/api/v1/fs/ls", params=params)
        result = payload["result"]
        if not isinstance(result, list):
            raise ValueError(f"OpenViking ls expected list; raw={raw[:500]!r}")
        if type(payload.get("has_more")) is not bool:
            raise ValueError(f"OpenViking ls missing has_more; raw={raw[:500]!r}")
        nodes: list[_Node] = []
        for entry in result:
            row = _as(_Node, entry, raw)
            prefix = uri.rstrip("/") + "/"
            if not row.uri.startswith(prefix):
                raise ValueError(f"OpenViking ls returned URI outside requested directory; raw={raw[:500]!r}")
            child = row.uri[len(prefix):].rstrip("/")
            if not child or "/" in child or child != row.name:
                raise ValueError(f"OpenViking ls returned invalid direct child; raw={raw[:500]!r}")
            nodes.append(row)
        return nodes, payload["has_more"]

    async def browse(self, scene: str, path: str, *, scope: Literal["scene", "public"] = "scene",
                     offset: int = 0, limit: int = 20) -> MemoryPage:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("browse requires offset >= 0 and limit 1..100")
        identity = await self._identity(scene)
        scene_root = f"viking://user/{identity.user_id}"
        uri = scene_root if scope == "scene" and not path else self._uri(identity, path, scope)
        entries, has_more = await self._list_directory(identity, uri, offset=offset, limit=limit)
        nodes: list[MemoryNode] = []
        restricted_children = ({"memories", "peers"} if scope == "scene" and not path
                               else {"memories"} if scope == "scene" and
                               re.fullmatch(r"peers/[1-9][0-9]*", path) is not None else None)
        for row in entries:
            if restricted_children is not None:
                if row.name not in restricted_children:
                    continue
                if not row.isDir:
                    raise ValueError(f"OpenViking memory root child is not a directory; entry={row.model_dump()!r}")
            nodes.append(MemoryNode(path=self._path_from_uri(identity, row.uri, scope),
                                    name=row.name, is_dir=row.isDir, access=row.access))
        return MemoryPage(nodes=tuple(nodes), has_more=has_more)

    async def memory_directories(self, scene: str) -> tuple[str, ...]:
        """Discover existing scene and peer memory roots, including every listing page."""
        async def children(path: str) -> list[MemoryNode]:
            nodes: list[MemoryNode] = []
            offset = 0
            while True:
                page = await self.browse(scene, path, offset=offset, limit=100)
                nodes.extend(page.nodes)
                if not page.has_more:
                    return nodes
                # browse filters unrelated entries; advance by the server page size.
                offset += 100

        directories: list[str] = []
        for node in await children(""):
            if node.path == "memories":
                directories.append(node.path)
            elif node.path == "peers":
                for peer in await children("peers"):
                    if not peer.is_dir:
                        raise ValueError(f"OpenViking peer is not a directory: {peer.path!r}")
                    directories.extend(child.path for child in await children(peer.path))
        return tuple(directories)

    async def read(self, scene: str, path: str, *,
                   scope: Literal["scene", "public"] = "scene") -> MemoryDocument:
        identity = await self._identity(scene)
        uri = self._uri(identity, path, scope, file=True)
        payload, raw = await self._request(identity, "GET", "/api/v1/content/read",
                                           params={"uri": uri, "raw": "true"})
        result = payload["result"]
        if not isinstance(result, str):
            raise ValueError(f"OpenViking read expected text; raw={raw[:500]!r}")
        return MemoryDocument(path=path, content=result)

    def _overview_uri(self, identity: SceneIdentity, path: str) -> str:
        uri = self._uri(identity, path, "scene")
        parts = path.split("/")
        if parts[0] == "peers" and (len(parts) < 3 or parts[2] != "memories"):
            raise ValueError("overview requires memories or peers/<QQ>/memories directory")
        if path.endswith(".md"):
            raise ValueError("overview requires a directory, not a memory file")
        return uri

    async def overview(self, scene: str, path: str = "memories") -> NativeOverview:
        identity = await self._identity(scene)
        uri = self._overview_uri(identity, path)
        offset = 0
        while True:
            entries, has_more = await self._list_directory(identity, uri, offset=offset,
                                                         limit=100, show_hidden=True)
            overview = next((entry for entry in entries if entry.name == ".overview.md"), None)
            if overview is not None:
                if overview.isDir:
                    raise ValueError(f"OpenViking overview is not a file; entry={overview.model_dump()!r}")
                break
            if not has_more:
                return NativeOverview(path, None, None)
            offset += 100
        payload, raw = await self._request(identity, "GET", "/api/v1/content/read",
                                           params={"uri": uri + "/.overview.md", "raw": "true"})
        if not isinstance(payload["result"], str):
            raise ValueError(f"OpenViking overview expected text; raw={raw[:1000]!r}")
        return parse_overview(payload["result"], uri=uri, path=path)

    async def refresh_overview(self, scene: str, path: str = "memories") -> OverviewRefresh:
        identity = await self._identity(scene)
        uri = self._overview_uri(identity, path)
        payload, raw = await self._request(identity, "POST", "/api/v1/content/reindex",
                                           body={"uri": uri, "mode": "semantic_and_vectors",
                                                 "recursive": True, "wait": True})
        return parse_refresh(payload["result"], uri=uri, raw=raw)

    async def history(self, scene: str, path: str) -> list[dict]:
        identity = await self._identity(scene)
        uri = self._uri(identity, path, "scene", file=True)
        payload, raw = await self._request(identity, "GET", "/api/v1/snapshot/log",
                                            params={"paths": uri, "limit": 100})
        if not isinstance(payload["result"], list):
            raise ValueError(f"OpenViking snapshot log expected list; raw={raw[:500]!r}")
        return [{"source": "snapshot", "path": path, **_as(_SnapshotEntry, entry, raw).model_dump()}
                for entry in payload["result"]]

    async def history_diff(self, scene: str, path: str, target: str, previous: str | None) -> dict:
        identity = await self._identity(scene)
        uri = self._uri(identity, path, "scene", file=True)
        params = {"path": uri, "to": target, "raw": "true"}
        if previous is not None:
            params["from"] = previous
        payload, raw = await self._request(identity, "GET", "/api/v1/snapshot/diff", params=params)
        diff = _as(_SnapshotDiff, payload["result"], raw)
        if diff.path != uri:
            raise ValueError(f"OpenViking snapshot diff returned another path; raw={raw[:500]!r}")
        return {**diff.model_dump(), "path": path}

    async def write(self, scene: str, path: str, content: str, *,
                    scope: Literal['scene', 'public'] = 'scene') -> MemoryWrite:
        identity = await self._identity(scene)
        uri = self._uri(identity, path, scope, file=True)
        payload, raw = await self._request(identity, "POST", "/api/v1/content/write", body={
            "uri": uri, "content": content, "mode": "replace", "wait": True,
            "timeout": self.settings.timeout_seconds,
        })
        row = _as(_Write, payload["result"], raw)
        expected = 'memory' if scope == 'scene' else 'resource'
        if row.uri != uri or row.context_type != expected or not row.content_updated:
            raise ValueError(f"OpenViking write did not confirm {scope} {expected} content update; raw={raw[:500]!r}")
        if row.vector_status not in {"complete", "skipped"} or row.semantic_status not in {"complete", "skipped"}:
            raise ValueError(f"OpenViking memory write refresh incomplete after wait=true; raw={raw[:1000]}")
        if row.overview_status not in {None, "complete", "skipped"}:
            raise ValueError(f"OpenViking memory overview refresh incomplete after wait=true; raw={raw[:1000]}")
        return MemoryWrite(path=path, content_updated=True, overview_status=row.overview_status,
                           semantic_status=row.semantic_status, vector_status=row.vector_status)

    async def search(self, scene: str, query: str, limit: int = 10) -> list[MemoryHit]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("search requires a nonblank query and limit 1..100")
        identity = await self._identity(scene)
        private, private_raw = await self._request(identity, "POST", "/api/v1/search/find", body={
            "query": query,
            "target_uri": [f"viking://user/{identity.user_id}/memories",
                           f"viking://user/{identity.user_id}/peers"],
            "context_type": "memory", "level": [2], "limit": limit,
        })
        private_result = _as(_Find, private["result"], private_raw)
        if private_result.resources or private_result.skills:
            raise ValueError(f"OpenViking scene memory search returned other context types; raw={private_raw[:500]!r}")
        hits = [MemoryHit(scope="scene", path=self._path_from_uri(identity, row.uri, "scene"),
                          score=row.score, abstract=row.abstract)
                for row in private_result.memories if row.level == 2 and row.context_type == "memory"]
        if len(hits) != len(private_result.memories):
            raise ValueError(f"OpenViking scene memory search returned unexpected level/type; raw={private_raw[:500]!r}")
        if self.settings.public_root is not None:
            public, public_raw = await self._request(identity, "POST", "/api/v1/search/find", body={
                "query": query, "target_uri": self.settings.public_root,
                "context_type": "resource", "level": [2], "limit": limit,
            })
            public_result = _as(_Find, public["result"], public_raw)
            if public_result.memories or public_result.skills:
                raise ValueError(f"OpenViking public search returned other context types; raw={public_raw[:500]!r}")
            public_hits = [MemoryHit(scope="public", path=self._path_from_uri(identity, row.uri, "public"),
                                     score=row.score, abstract=row.abstract)
                           for row in public_result.resources if row.level == 2 and row.context_type == "resource"]
            if len(public_hits) != len(public_result.resources):
                raise ValueError(f"OpenViking public search returned unexpected level/type; raw={public_raw[:500]!r}")
            hits.extend(public_hits)
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    async def delete(self, scene: str, path: str) -> MemoryDelete:
        identity = await self._identity(scene)
        uri = self._uri(identity, path, "scene", file=True)
        payload, raw = await self._request(identity, "DELETE", "/api/v1/fs", params={
            "uri": uri, "recursive": "false", "wait": "true", "timeout": self.settings.timeout_seconds,
        })
        row = _as(_Delete, payload["result"], raw)
        if row.uri != uri or row.semantic_status not in {None, "complete", "skipped"}:
            raise ValueError(f"OpenViking ordinary delete not fully confirmed; raw={raw[:1000]}")
        return MemoryDelete(path=path, semantic_status=row.semantic_status,
                            estimated_deleted_count=row.estimated_deleted_count)

    async def ingest(self, scene: str, messages: list[ChatMessage], *, persona_ids: list[str | None]) -> IngestReceipt:
        if not 1 <= len(messages) <= 100:
            raise ValueError("ingest requires 1..100 original messages")
        if len(persona_ids) != len(messages):
            raise ValueError('Native source messages and captured persona IDs must have equal lengths')
        identity = await self._identity(scene)
        session_id = messages[0].id
        if _IDENTIFIER.fullmatch(session_id) is None:
            raise ValueError(f"original message id cannot be a session path segment: {session_id!r}")
        batch: list[dict[str, object]] = []
        for message, persona_id in zip(messages, persona_ids, strict=True):
            if message.scene != scene:
                raise ValueError(f"ingest message {message.id} belongs to {message.scene!r}, not {scene!r}")
            if message.is_self:
                if message.send_status not in {"sent", "received"}:
                    raise ValueError(f"ingest Bot message {message.id} was not confirmed as sent")
                role, peer_id = "assistant", None
            else:
                if message.send_status != "received" or _QQ.fullmatch(message.sender.uid) is None:
                    raise ValueError(f"ingest sender/status invalid for original message {message.id}")
                role, peer_id = "user", message.sender.uid
                if persona_id is not None:
                    raise ValueError(f'Non-self original {message.id} cannot carry a Bot persona ID: {persona_id!r}')
            batch.append({
                "role": role, "peer_id": peer_id,
                "content": json.dumps({'message': render_message(message, timezone='UTC'), 'persona_id': persona_id},
                                      ensure_ascii=False, allow_nan=False),
                "created_at": datetime.fromtimestamp(message.time, timezone.utc).isoformat(),
                "source_message_ids": [message.id],
            })
        creation: dict[str, object] = {'session_id': session_id, 'auto_commit_policy': None}
        if self.settings.memory_policy is not None:
            creation['memory_policy'] = self.settings.memory_policy.wire()
        created_payload, created_raw = await self._request(identity, "POST", "/api/v1/sessions", body=creation)
        created = created_payload["result"]
        if not isinstance(created, dict) or created.get("session_id") != session_id or created.get("auto_commit_policy") is not None:
            raise ValueError(f"OpenViking session creation did not disable automatic commit; raw={created_raw[:500]!r}")
        if self.settings.memory_policy is not None:
            saved, saved_raw = await self._request(identity, 'GET', f'/api/v1/sessions/{session_id}')
            metadata = saved['result']
            if not isinstance(metadata, dict) or metadata.get('session_id') != session_id:
                raise ValueError(f'OpenViking memory policy read returned another session; raw={saved_raw[:1000]!r}')
            policy = _as(NativeMemoryPolicy, metadata.get('memory_policy'), saved_raw)
            if policy.wire() != self.settings.memory_policy.wire():
                raise ValueError(f'OpenViking saved memory policy differs from requested policy; raw={saved_raw[:1000]!r}')
        added_payload, added_raw = await self._request(identity, "POST", f"/api/v1/sessions/{session_id}/messages/batch",
                                                       body={"messages": batch})
        added = added_payload["result"]
        if not isinstance(added, dict) or added.get("session_id") != session_id or added.get("added") != len(batch):
            raise ValueError(f"OpenViking did not confirm all archived source messages; raw={added_raw[:500]!r}")
        committed, commit_raw = await self._request(identity, "POST", f"/api/v1/sessions/{session_id}/commit",
                                                    body={"keep_recent_count": 0})
        receipt = _as(_Commit, committed["result"], commit_raw)
        if receipt.session_id != session_id or (receipt.status == "accepted") != receipt.archived:
            raise ValueError(f"OpenViking inconsistent commit receipt; raw={commit_raw[:500]!r}")
        if receipt.status == "accepted" and (not receipt.task_id or not receipt.archive_uri):
            raise ValueError(f"OpenViking accepted commit without task/archive reference; raw={commit_raw[:500]!r}")
        return IngestReceipt(status=receipt.status, session_id=session_id,
                             task_id=receipt.task_id, archive_uri=receipt.archive_uri,
                             memory_policy=None if self.settings.memory_policy is None
                             else self.settings.memory_policy.wire())

    async def ingest_status(self, scene: str, task_id: str) -> IngestTask:
        if _IDENTIFIER.fullmatch(task_id) is None:
            raise ValueError("task_id must be an OpenViking task path segment")
        identity = await self._identity(scene)
        result, raw = await self._request(identity, "GET", f"/api/v1/tasks/{task_id}")
        return parse_ingest_task(result["result"], task_id=task_id, raw=raw)
