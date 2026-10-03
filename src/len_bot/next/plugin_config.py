"""Configured plugin paths, values and enabled installation names."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


PLUGIN_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


PLUGIN_RESERVED = frozenset({"paths", "data_directory", "disabled"})


class PluginSettings(BaseModel):
    """Paths, data directory and disabled names are settings; extra keys are plugin values."""
    model_config = ConfigDict(extra="allow", strict=True, hide_input_in_errors=True)

    paths: list[Path] = Field(default_factory=list)
    data_directory: Path
    disabled: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def plugin_entries(self) -> PluginSettings:
        if len(set(self.paths)) != len(self.paths):
            raise ValueError("plugins.paths must not repeat a directory")
        if len(set(self.disabled)) != len(self.disabled) or any(name not in self.configured for name in self.disabled):
            raise ValueError("plugins.disabled 必须是不重复的已配置插件名")
        self.disabled.sort()
        for name, values in (self.model_extra or {}).items():
            if PLUGIN_NAME.fullmatch(name) is None:
                raise ValueError(f"plugins.{name}: plugin names use lowercase letters, digits and underscores")
            if not isinstance(values, dict) or not all(isinstance(key, str) for key in values):
                raise ValueError(f"plugins.{name} must be an object of configuration values")
        return self

    @property
    def configured(self) -> dict[str, dict]:
        return dict(self.model_extra or {})
