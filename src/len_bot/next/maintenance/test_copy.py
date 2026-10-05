"""Copy a stopped instance for simulated panel tests."""

import argparse
import json
from pathlib import Path
import shutil

from ..config import HostConfig
from ..instance_lock import instance_lock


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
        shutil.copytree(source, destination, symlinks=True)
        path = destination / 'lenbot.config.json'
        value = _relocate(json.loads(path.read_text(encoding='utf-8')), source, destination)
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
