"""Initialize a new instance from setup JSON on stdin, without running the host."""

import argparse
import json
from pathlib import Path
import sys

from ..instance_lock import instance_lock
from ..panel.setup import FirstSetup, initialize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    root = Path.cwd()
    with instance_lock(root):
        item = FirstSetup.model_validate_json(sys.stdin.read())
        result = initialize(root, item)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
