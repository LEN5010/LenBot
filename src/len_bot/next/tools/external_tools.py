"""Low-frequency tools supplied by loaded plugins or connected MCP servers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .discovery import model_schema


@dataclass(frozen=True)
class ExternalTool:
    name: str
    description: str
    parameters: dict
    # Shown in results and the panel, for example "插件 group_digest" or "MCP 服务 files".
    source: str
    call: Callable[[str, dict], Awaitable[str]] = field(repr=False, compare=False)

    summary: str | None = None
    instructions: str | None = None

    @property
    def discovery(self) -> dict:
        return {"name": self.name, "description": self.description if self.summary is None else self.summary,
                "source": self.source}

    @property
    def definition(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": f"[{self.source}] {self.description}",
            "parameters": model_schema(self.parameters),
        }}
