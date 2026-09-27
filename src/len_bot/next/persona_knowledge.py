"""Read one role's Markdown knowledge snapshot and page it by actual filename."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from string import Template
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml


@dataclass(frozen=True, slots=True)
class PersonaDocument:
    content: str
    tags: tuple[str, ...]


def _frontmatter(path: Path, content: str) -> tuple[str, ...]:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return ()
    closing = next((position for position, line in enumerate(lines[1:], 1)
                    if line.rstrip("\r\n") == "---"), None)
    if closing is None:
        raise ValueError(f"{path}: unclosed YAML frontmatter; raw={content[:200]!r}")
    source = "".join(lines[1:closing])
    try:
        header = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: invalid YAML frontmatter: {error}; raw={source[:200]!r}") from error
    if header is None:
        return ()
    if not isinstance(header, dict):
        raise ValueError(f"{path}: YAML frontmatter must be an object; raw={source[:200]!r}")
    if "tags" not in header:
        return ()
    tags = header["tags"]
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError(f"{path}: frontmatter tags must be a list of text; raw={source[:200]!r}")
    return tuple(tags)


def _document(path: Path) -> PersonaDocument:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(f"{path}: cannot read knowledge file: {error}") from error
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as error:
        snippet = data[max(0, error.start - 30):error.end + 30]
        raise ValueError(f"{path}: invalid UTF-8: {error}; raw={snippet!r}") from error
    return PersonaDocument(content=content, tags=_frontmatter(path, content))


def load_knowledge(persona_root: Path) -> dict[str, PersonaDocument]:
    """Load only actual files beneath knowledge/, preserving every source byte as text."""
    root = persona_root.resolve()
    directory = root / "knowledge"
    if not directory.exists() and not directory.is_symlink():
        return {}
    try:
        actual = directory.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{directory}: cannot resolve knowledge directory: {error}") from error
    if not actual.is_relative_to(root):
        raise ValueError(f"{directory}: knowledge directory points outside persona package: {actual}")
    if directory.is_symlink():
        raise ValueError(f"{directory}: knowledge directory symlink is not followed")
    if not directory.is_dir():
        raise ValueError(f"{directory}: knowledge path is not a directory")

    documents: dict[str, PersonaDocument] = {}

    def listing_error(error: OSError) -> None:
        raise ValueError(f"{error.filename or directory}: cannot list knowledge directory: {error}") from error

    for folder, _, filenames in os.walk(directory, followlinks=False, onerror=listing_error):
        for filename in filenames:
            if not filename.endswith(".md"):
                continue
            path = Path(folder) / filename
            try:
                target = path.resolve(strict=True)
            except OSError as error:
                raise ValueError(f"{path}: cannot resolve Markdown knowledge file: {error}") from error
            if not target.is_relative_to(actual):
                raise ValueError(f"{path}: Markdown file points outside knowledge directory: {target}")
            if not target.is_file():
                raise ValueError(f"{path}: Markdown knowledge path is not a regular file")
            documents[path.relative_to(directory).as_posix()] = _document(path)

    return dict(sorted(documents.items()))


class PersonaKnowledgeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["list", "search", "read"] = "list"
    query: str | None = None
    filename: str | None = None
    offset: int = Field(default=0, ge=0, strict=True)

    @field_validator("query", "filename")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("query and filename must not be blank")
        return value

    @model_validator(mode="after")
    def operation_fields(self) -> PersonaKnowledgeArguments:
        if self.action == "list":
            if self.query is not None or self.filename is not None:
                raise ValueError("list does not accept query or filename")
        elif self.action == "search":
            if self.query is None or self.filename is not None:
                raise ValueError("search requires query and does not accept filename")
        elif self.filename is None or self.query is not None:
            raise ValueError("read requires filename and does not accept query")
        return self


PERSONA_KNOWLEDGE_TOOL = {"type": "function", "function": {
    "name": "persona_knowledge",
    "description": "按需查当前角色设定资料；list列文件名和标签，search按文件名/标签/全文原词匹配，"
                   "两者每页5项、offset是条目位置；read用真实filename每页读4000字符、offset是原文字符位置。"
                   "按next_offset续页；搜索preview不是全文，content_offset是原文命中位置。",
    "parameters": PersonaKnowledgeArguments.model_json_schema(),
}}
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_persona_knowledge.md"


def _page(items: list[dict], offset: int) -> tuple[list[dict], int | None]:
    if offset > len(items):
        raise ValueError(f"offset {offset} exceeds result count {len(items)}")
    end = min(offset + 5, len(items))
    return items[offset:end], end if end < len(items) else None


def persona_knowledge(persona_id: str, persona_name: str,
                      documents: dict[str, PersonaDocument],
                      arguments: PersonaKnowledgeArguments) -> str:
    filenames = sorted(documents)
    if arguments.action == "list":
        items = [{"filename": name, "tags": list(documents[name].tags)} for name in filenames]
        page, next_offset = _page(items, arguments.offset)
        result = {"action": "list", "offset": arguments.offset,
                  "next_offset": next_offset, "documents": page}
    elif arguments.action == "search":
        pattern = re.compile(re.escape(arguments.query), re.IGNORECASE)
        matches = []
        for name in filenames:
            document = documents[name]
            body_match = pattern.search(document.content)
            if body_match is None and pattern.search(name) is None and not any(
                    pattern.search(tag) for tag in document.tags):
                continue
            position = 0 if body_match is None else body_match.start()
            matches.append({"filename": name, "tags": list(document.tags),
                            "content_offset": position, "total_chars": len(document.content),
                            "preview": document.content[position:position + 240]})
        page, next_offset = _page(matches, arguments.offset)
        result = {"action": "search", "query": arguments.query, "offset": arguments.offset,
                  "next_offset": next_offset, "matches": page}
    else:
        name = arguments.filename
        if name not in documents:
            raise ValueError(f"Current persona has no knowledge file {name!r}")
        document = documents[name]
        if arguments.offset > len(document.content):
            raise ValueError(
                f"offset {arguments.offset} exceeds {name!r} length {len(document.content)}"
            )
        end = min(arguments.offset + 4000, len(document.content))
        result = {"action": "read", "filename": name, "tags": list(document.tags),
                  "offset": arguments.offset, "total_chars": len(document.content),
                  "next_offset": end if end < len(document.content) else None,
                  "text": document.content[arguments.offset:end]}
    return Template(PROMPT.read_text(encoding="utf-8")).substitute(
        persona_id=persona_id, persona_name=persona_name,
        result=json.dumps(result, ensure_ascii=False, allow_nan=False),
    )
