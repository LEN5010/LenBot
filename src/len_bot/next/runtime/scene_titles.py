"""Group names and private nicknames, read from the platform once per process.

They label scenes for the panel and for host management; they are not stored.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..platform.platform_tools import scene_title

if TYPE_CHECKING:
    from .network import NetworkRuntime


class SceneTitles:
    def __init__(self, runtime: NetworkRuntime):
        self.runtime = runtime
        self.titles: dict[str, str] = {}

    async def read(self) -> tuple[dict[str, str], dict[str, str]]:
        """Titles of configured scenes, and the original error for scenes that could not be read."""
        scenes = list(self.runtime.config.scenes)
        platform = self.runtime.platform
        missing = [scene for scene in scenes if scene not in self.titles]
        if platform is None or not platform.connected:
            missing = []
        results = await asyncio.gather(*(scene_title(scene, platform.call) for scene in missing),
                                       return_exceptions=True)
        errors = {}
        for scene, result in zip(missing, results):
            if isinstance(result, Exception):
                errors[scene] = f"{type(result).__name__}: {result}"
            elif isinstance(result, BaseException):
                raise result
            else:
                self.titles[scene] = result
        return {scene: self.titles[scene] for scene in scenes if scene in self.titles}, errors
