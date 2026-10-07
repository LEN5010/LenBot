"""Published LenBot releases compared with the running version; reading only, nothing is installed.

Replacing the program stays with the installation's updater (deployment packages and Docker) or with
Git and the offline migrations (source checkouts). This module only answers "is there a newer one".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from importlib.metadata import version as installed_version
import logging
import time

from packaging.version import InvalidVersion, Version

from .logs import log_event

logger = logging.getLogger(__name__)

GITHUB_RELEASES = 'https://api.github.com/repos/lendevs/LenBot/releases?per_page=100'
CHECK_TIMEOUT_SECONDS = 15.0
CHECK_INTERVAL_SECONDS = 24 * 3600.0
# The first background check waits, so it never delays a starting host or touches the network
# from a host that only runs for a moment (tests, a quick restart).
FIRST_CHECK_DELAY_SECONDS = 60.0


def current_version() -> str:
    """The single version source: pyproject.toml, as recorded in the installed package metadata."""
    return installed_version('len-bot')


def github_releases(raw: object) -> list[dict]:
    """Published ``v<version>`` releases from the GitHub API list, newest version first."""
    if not isinstance(raw, list):
        raise ValueError(f'GitHub releases response is not a list: {str(raw)[:200]}')
    releases = []
    for item in raw:
        if item['draft']:
            continue
        tag = item['tag_name']
        if not tag.startswith('v'):
            continue
        try:
            Version(tag[1:])
        except InvalidVersion:
            continue
        releases.append({'tag': tag, 'version': tag[1:], 'prerelease': item['prerelease'],
                         'notes': item['body'] or ''})
    return sorted(releases, key=lambda release: Version(release['version']), reverse=True)


def compare(releases: list[dict], current: str) -> dict:
    """Mark each release newer or not; name the newest stable and a still newer prerelease."""
    running = Version(current)
    marked = sorted(({**release, 'newer': Version(release['version']) > running} for release in releases),
                    key=lambda release: Version(release['version']), reverse=True)
    stable = next((release for release in marked if not release['prerelease']), None)
    floor = running if stable is None else max(running, Version(stable['version']))
    preview = next((release for release in marked
                    if release['prerelease'] and Version(release['version']) > floor), None)
    return {
        'releases': marked,
        'latest': None if stable is None else stable['version'],
        'update_available': stable is not None and stable['newer'],
        'latest_prerelease': None if preview is None else preview['version'],
    }


class ReleaseCheck:
    """The last answer to "is there a newer release", refreshed on request and once a day in the background."""

    def __init__(self, load: Callable[[], Awaitable[list[dict]]], *, enabled: bool, current: str | None = None):
        self.load = load
        self.enabled = enabled
        self.current = current_version() if current is None else current
        self.checked_at: float | None = None
        self.error: str | None = None
        self.summary: dict | None = None

    async def refresh(self) -> list[dict]:
        """Ask now; the answer or the error is kept for state() and the error still reaches the caller."""
        try:
            async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
                releases = await self.load()
            compared = compare(releases, self.current)
        except Exception as error:
            self.checked_at, self.error = time.time(), f'{type(error).__name__}: {error}'
            raise
        self.checked_at, self.error = time.time(), None
        self.summary = {key: value for key, value in compared.items() if key != 'releases'}
        return compared['releases']

    def state(self) -> dict:
        summary = self.summary or {'latest': None, 'update_available': None, 'latest_prerelease': None}
        return {'enabled': self.enabled, 'current': self.current, 'checked_at': self.checked_at,
                'error': self.error, **summary}

    async def run(self) -> None:
        """Background loop for the panel's lifetime; a failed check is logged and tried again next interval."""
        await asyncio.sleep(FIRST_CHECK_DELAY_SECONDS)
        while True:
            try:
                await self.refresh()
            except Exception:
                log_event(logger, 'release_check', '检查新版本失败，下个周期再试', level=logging.WARNING,
                          error=self.error, current=self.current)
            else:
                log_event(logger, 'release_check', current=self.current, **self.summary)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
