"""Explicit stopped-root rebuild of the selected local memory index."""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import sys

from .config import SharedConfig, load_instance_config
from .memory import LocalMemoryConfig, open_memory_backend
from .store import encode


def _backup(index: Path) -> Path | None:
    if not index.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = index.with_name(index.name + f".before-reindex-{stamp}.bak")
    with backup.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
    except BaseException:
        backup.unlink()
        raise
    return backup


async def rebuild(config: SharedConfig) -> dict:
    if not isinstance(config.memory, LocalMemoryConfig):
        raise ValueError("Memory reindex requires memory.backend=local in the root configuration")
    # Stopping the host is an operator precondition, not a claim inferred from
    # an idle SQLite connection. The Markdown files themselves are not rewritten.
    backup = await asyncio.to_thread(_backup, config.memory.local.directory / ".memory-index.sqlite3")
    async with open_memory_backend(config) as backend:
        if config.memory.local.embedding is None:
            count = await asyncio.to_thread(backend.reindex)
        else:
            count = await backend.reindex_embeddings()
    return {"backend": "local", "directory": str(config.memory.local.directory),
            "indexed_files": count,
            "retrieval": "text" if config.memory.local.embedding is None else "hybrid",
            "backup": None if backup is None else str(backup),
            "markdown_modified": False, "history_preserved": True}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Memory reindex takes no arguments; stop the host and run from its root")
    config = load_instance_config(Path.cwd())
    print(encode(asyncio.run(rebuild(config))), flush=True)


if __name__ == "__main__":
    main()
