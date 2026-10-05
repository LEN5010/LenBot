"""Copy a stopped instance for simulated panel tests.

The instance may live in a source checkout, so only the configuration and the
files it references inside the instance root are copied, not the code around it.
"""

import argparse
import json
from pathlib import Path
import shutil

from ..config import HostConfig
from ..instance_lock import instance_lock


def _referenced(value, source: Path) -> set[Path]:
    """Existing files and directories inside the instance that configuration values point at."""
    if isinstance(value, dict):
        return set().union(*(_referenced(item, source) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_referenced(item, source) for item in value))
    if not isinstance(value, str) or not value or '\n' in value:
        return set()
    path = Path(value) if value.startswith('/') else source / value
    if path != source and path.is_relative_to(source) and (path.exists() or path.is_symlink()):
        return {path.relative_to(source)}
    return set()


def _relocate(value, source: Path, destination: Path):
    """Point absolute paths inside the source instance at the copy, so tests never write to it."""
    if isinstance(value, dict):
        return {name: _relocate(item, source, destination) for name, item in value.items()}
    if isinstance(value, list):
        return [_relocate(item, source, destination) for item in value]
    if isinstance(value, str) and value.startswith('/') and Path(value).is_relative_to(source):
        return str(destination / Path(value).relative_to(source))
    return value


def copy_instance(source: Path, destination: Path, panel_port: int) -> None:
    with instance_lock(source):
        config = source / 'lenbot.config.json'
        value = json.loads(config.read_text(encoding='utf-8'))
        database = source / value['database']
        # SQLite WAL files and the memory job database sit next to the business database.
        paths = _referenced(value, source) | {
            path.relative_to(source) for path in database.parent.glob(database.name + '*')}
        destination.mkdir()
        shutil.copy2(config, destination / config.name)
        for relative in sorted(paths):
            if any(parent in paths for parent in relative.parents):
                continue
            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
            original = source / relative
            if original.is_dir() and not original.is_symlink():
                shutil.copytree(original, destination / relative, symlinks=True)
            else:
                shutil.copy2(original, destination / relative, follow_symlinks=False)
        path = destination / config.name
        value = _relocate(value, source, destination)
        value['delivery'] = 'simulated'
        value['onebot'] = None
        value['panel']['port'] = panel_port
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        HostConfig.model_validate_json(text)
        path.write_text(text, encoding='utf-8')
    print(f'Source: {source}; destination: {destination}; panel port: {panel_port}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--panel-port', type=int, required=True)
    args = parser.parse_args()
    copy_instance(args.source.resolve(), args.destination.absolute(), args.panel_port)


if __name__ == '__main__':
    main()
