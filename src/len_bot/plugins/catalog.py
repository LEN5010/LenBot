"""Discover plugin-owned descriptions before parsing their root-file settings."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from pydantic import BaseModel

from len_bot.plugins.models import PluginPermission, PluginType

if TYPE_CHECKING:
    from len_bot.config_store import RootConfig
    from len_bot.plugins.base import BasePlugin, PluginContext


@dataclass(frozen=True)
class PluginSpec:
    id: str
    name: str
    description: str
    version: str
    config_model: type[BaseModel]
    create: Callable[[PluginContext], BasePlugin]
    permissions: tuple[PluginPermission, ...] = ()
    plugin_type: PluginType = PluginType.HYBRID
    private_tools: bool = False
    call_timeout: Callable[[BaseModel], float] | None = None
    validate_config: Callable[[BaseModel, RootConfig], None] | None = None


@dataclass(frozen=True)
class DiscoveredPlugin:
    spec: PluginSpec
    directory: Path
    module_name: str


class PluginCatalog:
    def __init__(self, entries: dict[str, DiscoveredPlugin], directories: tuple[Path, ...]):
        self.entries = entries
        self.directories = directories

    @classmethod
    def discover(cls, directories: list[str]) -> PluginCatalog:
        builtin = Path(__file__).parent / 'builtin'
        roots = [builtin.resolve(), *(Path(value).expanduser().resolve() for value in directories)]
        if len(set(roots)) != len(roots):
            raise ValueError('plugin_directories contains a repeated plugin root')
        entries = {}
        for index, root in enumerate(roots):
            if not root.is_dir():
                raise ValueError(f'Plugin directory does not exist: {root}')
            for folder in sorted(root.iterdir()):
                entry_file = folder / '__init__.py'
                if not folder.is_dir() or not entry_file.is_file():
                    continue
                if not folder.name.isidentifier():
                    raise ValueError(f'Plugin directory needs a Python package name: {folder}')
                module_name = (f'len_bot.plugins.builtin.{folder.name}' if index == 0
                               else f'lenbot_local_{index}_{folder.name}')
                if index == 0:
                    module = importlib.import_module(module_name)
                else:
                    definition = importlib.util.spec_from_file_location(module_name, entry_file,
                        submodule_search_locations=[str(folder)])
                    module = importlib.util.module_from_spec(definition)
                    sys.modules[module_name] = module
                    try:
                        definition.loader.exec_module(module)
                    except BaseException:
                        sys.modules.pop(module_name, None)
                        raise
                spec = getattr(module, 'PLUGIN', None)
                if not isinstance(spec, PluginSpec) or not spec.id or not spec.id.isidentifier():
                    raise ValueError(f'{entry_file} must export PLUGIN: PluginSpec with a unique identifier')
                if not issubclass(spec.config_model, BaseModel):
                    raise TypeError(f'{entry_file}: config_model must be a Pydantic model')
                if spec.id in entries:
                    raise ValueError(f'Plugin {spec.id!r} conflicts: {folder} and {entries[spec.id].directory}')
                entries[spec.id] = DiscoveredPlugin(spec, folder.resolve(), module_name)
        return cls(entries, tuple(roots[1:]))
