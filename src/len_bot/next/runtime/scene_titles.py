"""Group names, member counts and private nicknames read from the platform.

They label scenes for the panel and for host management. The last values read are kept in
`state/scene-titles.json` so the panel still shows names while QQ is disconnected; each new
connection reads every scene once more.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..platform.platform_tools import scene_title

if TYPE_CHECKING:
    from .network import NetworkRuntime

logger = logging.getLogger(__name__)


class SceneTitles:
    def __init__(self, runtime: NetworkRuntime):
        self.runtime = runtime
        root = runtime.config._instance_root
        self.path: Path | None = None if root is None else root / "state" / "scene-titles.json"
        self.titles: dict[str, str] = {}
        self.members: dict[str, int] = {}
        self._load()
        # Scenes read during the connection that began at `connection`.
        self.connection: float | None = None
        self.fresh: set[str] = set()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.titles = {str(scene): str(title) for scene, title in saved["titles"].items()}
            self.members = {str(scene): int(count) for scene, count in saved["members"].items()}
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning("群名缓存读取失败，将重新读取：%s: %s", type(error).__name__, error)
            self.titles, self.members = {}, {}

    def _save(self) -> None:
        if self.path is None:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"titles": self.titles, "members": self.members},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            logger.warning("群名缓存写入失败：%s: %s", type(error).__name__, error)

    async def read(self, extra: Iterable[str] = ()) -> tuple[dict[str, str], dict[str, str]]:
        """Titles of configured scenes plus `extra`, and the original error for scenes that could not be read."""
        scenes = list(dict.fromkeys([*self.runtime.config.scenes, *extra]))
        if self.connection != self.runtime.connected_since:
            self.connection, self.fresh = self.runtime.connected_since, set()
        platform = self.runtime.platform
        missing = ([] if platform is None or not platform.connected
                   else [scene for scene in scenes if scene not in self.fresh])
        results = await asyncio.gather(*(scene_title(scene, platform.call) for scene in missing),
                                       return_exceptions=True)
        errors = {}
        for scene, result in zip(missing, results):
            if isinstance(result, Exception):
                errors[scene] = f"{type(result).__name__}: {result}"
            elif isinstance(result, BaseException):
                raise result
            else:
                self.titles[scene], members = result
                if members is not None:
                    self.members[scene] = members
                self.fresh.add(scene)
        if len(errors) < len(missing):
            self._save()
        return {scene: self.titles[scene] for scene in scenes if scene in self.titles}, errors
