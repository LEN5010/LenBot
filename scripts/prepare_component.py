"""Fetch one pinned component and apply this release's explicit patch."""

import argparse
import json
from pathlib import Path
import subprocess
import tarfile

PROJECT = Path(__file__).resolve().parents[1]


def prepare(name: str, destination: Path) -> dict:
    component = json.loads((PROJECT / 'deploy/components.json').read_text(encoding='utf-8'))[name]
    destination.mkdir(parents=True)
    def git(*arguments: str) -> None:
        subprocess.run(['git', '-C', str(destination), *arguments], check=True)
    git('init', '--quiet')
    git('remote', 'add', 'origin', component['repository'])
    git('fetch', '--depth=1', 'origin', component['revision'])
    git('checkout', '--detach', 'FETCH_HEAD')
    git('apply', str(PROJECT / component['patch']))
    return component


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=('browserskill',))
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source-archive', type=Path)
    args = parser.parse_args()
    component = prepare(args.component, args.destination)
    if args.source_archive is not None:
        def source_only(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            return None if '.git' in Path(info.name).parts else info
        with tarfile.open(args.source_archive, 'x:gz') as archive:
            archive.add(args.destination, arcname=args.component, filter=source_only)
    print(json.dumps(component, ensure_ascii=False))


if __name__ == '__main__':
    main()
