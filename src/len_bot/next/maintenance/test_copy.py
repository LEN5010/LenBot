"""Copy a stopped instance for simulated panel tests."""

import argparse
import json
from pathlib import Path
import shutil

from ..config import HostConfig
from ..instance_lock import instance_lock


def copy_instance(source: Path, destination: Path, panel_port: int) -> None:
    with instance_lock(source):
        shutil.copytree(source, destination, symlinks=True)
        path = destination / 'lenbot.config.json'
        value = json.loads(path.read_text(encoding='utf-8'))
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
