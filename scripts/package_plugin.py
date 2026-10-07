"""Build one plugin's importable ZIP from its tracked runtime files; shared by every plugin repository's CI.

    python scripts/package_plugin.py PLUGIN_ROOT OUTPUT.zip [--tag vX.Y.Z --notes NOTES.md]

With ``--tag`` the tag must equal ``v`` + the manifest version, and the matching
``# X.Y.Z`` section of the plugin's CHANGELOG.md is written to NOTES.md for the release.
Nothing is published.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile

RESOURCE_DIRECTORIES = {'prompts', 'skills', 'assets', 'fonts'}
EXCLUDED = {'pyproject.toml', 'AGENTS.md', 'catalog-entry.json'}


def runtime_files(root: Path) -> list[str]:
    """Tracked package files: top-level Python, manifest, docs and licenses, plus resource directories."""
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    selected = []
    for name in sorted(filter(None, tracked)):
        path = Path(name)
        if path.name in EXCLUDED:
            continue
        if path.parts[0] in RESOURCE_DIRECTORIES or len(path.parts) == 1 and (
                path.suffix in {'.py', '.toml', '.md'} or path.name in {'LICENSE', 'NOTICE'}):
            selected.append(name)
    return selected


def package(root: Path, output: Path) -> Path:
    manifest = tomllib.loads((root / 'plugin.toml').read_text(encoding='utf-8'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name in runtime_files(root):
            archive.write(root / name, name)
    with ZipFile(output) as archive:
        names = set(archive.namelist())
    for required in ('plugin.toml', '__init__.py'):
        if required not in names:
            raise ValueError(f'plugin package lacks tracked {required}')
    instructions = manifest.get('model', {}).get('instructions')
    if instructions is not None and instructions not in names:
        raise ValueError(f'model.instructions resource is not tracked: {instructions}')
    return output


def release_notes(root: Path, version: str) -> str:
    """The ``# version`` section of CHANGELOG.md, up to the next level-one heading."""
    lines = (root / 'CHANGELOG.md').read_text(encoding='utf-8').splitlines()
    try:
        start = lines.index(f'# {version}')
    except ValueError:
        raise ValueError(f'CHANGELOG.md has no "# {version}" section') from None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith('# ')), len(lines))
    return '\n'.join(lines[start + 1:end]).strip() + '\n'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--tag')
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.tag is not None:
        version = tomllib.loads((root / 'plugin.toml').read_text(encoding='utf-8'))['version']
        if args.tag != 'v' + version:
            raise ValueError(f'tag {args.tag} does not match plugin.toml version {version}')
        if args.notes is None:
            parser.error('--tag requires --notes')
        args.notes.write_text(release_notes(root, version), encoding='utf-8')
    print(package(root, args.output.resolve()))


if __name__ == '__main__':
    main()
