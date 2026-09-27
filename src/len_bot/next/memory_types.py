"""Backend-neutral facts returned by a memory directory or file read."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryNode:
    path: str
    name: str
    is_dir: bool
    access: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryPage:
    nodes: tuple[MemoryNode, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    path: str
    content: str
