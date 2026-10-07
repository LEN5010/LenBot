"""Copy plugin repositories' catalog-entry.json into the built-in catalog, pinned to a commit.

    uv run --no-sync python scripts/sync_plugin_catalog.py ../lenbot-plugin-asoul ../lenbot-plugin-bilibili@v1.1.0

Each argument is a local checkout, optionally ``@ref`` (default ``HEAD``). The entry is
read from that ref, its ``ref`` becomes the resolved commit, and name, version and
interface must match the ref's plugin.toml. The commit must already be on a remote
branch, otherwise users could not fetch it. Entries are replaced by name; new names
are appended. Nothing is pushed or committed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'src/len_bot/next/plugins/plugin_catalog.json'


def _git(repository: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(repository), *args], text=True)


def pinned_entry(repository: Path, ref: str = 'HEAD') -> dict:
    commit = _git(repository, 'rev-parse', '--verify', f'{ref}^{{commit}}').strip()
    entry = json.loads(_git(repository, 'show', f'{commit}:catalog-entry.json'))
    manifest = tomllib.loads(_git(repository, 'show', f'{commit}:plugin.toml'))
    for field in ('name', 'version', 'interface'):
        if entry.get(field) != manifest.get(field):
            raise ValueError(f'{repository} {ref}: catalog-entry.json 的 {field} 是 {entry.get(field)!r}，'
                             f'plugin.toml 是 {manifest.get(field)!r}')
    if not _git(repository, 'branch', '--remotes', '--contains', commit).strip():
        raise ValueError(f'{repository} {ref}: 提交 {commit[:12]} 还不在任何远端分支上，先推送再同步')
    return {**entry, 'ref': commit}


def merge(catalog: dict, entries: list[dict]) -> dict:
    current = list(catalog['entries'])
    positions = {entry['name']: index for index, entry in enumerate(current)}
    for entry in entries:
        if entry['name'] in positions:
            current[positions[entry['name']]] = entry
        else:
            positions[entry['name']] = len(current)
            current.append(entry)
    return {**catalog, 'entries': current}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('repositories', nargs='+', help='local checkout, optionally PATH@REF')
    args = parser.parse_args()
    entries = []
    for argument in args.repositories:
        path, _, ref = argument.partition('@')
        entries.append(pinned_entry(Path(path).resolve(), ref or 'HEAD'))
    catalog = merge(json.loads(CATALOG.read_text(encoding='utf-8')), entries)
    sys.path.insert(0, str(ROOT / 'src'))
    from len_bot.next.plugins.catalog import CatalogIndex
    CatalogIndex.model_validate(catalog)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for entry in entries:
        print(f"{entry['name']} {entry['version']} {entry['ref']}")


if __name__ == '__main__':
    main()
