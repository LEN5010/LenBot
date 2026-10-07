"""Push an architecture tag once, or retain an existing tag with the identical image configuration."""

import argparse
import json
import subprocess


def push(local: str, remote: str) -> None:
    image = subprocess.run(['docker', 'image', 'inspect', local, '--format', '{{.Id}}'],
                           capture_output=True, text=True, check=True).stdout.strip()
    existing = subprocess.run(['docker', 'buildx', 'imagetools', 'inspect', remote, '--raw'], capture_output=True, text=True)
    if existing.returncode == 0:
        manifest = json.loads(existing.stdout)
        if manifest['config']['digest'] != image:
            raise ValueError(f'{remote} already contains a different image; publish a new version instead')
        print(f'Retaining existing matching tag: {remote}')
        return
    missing = ('manifest unknown', 'not found', 'no such manifest')
    if not any(message in existing.stderr.lower() for message in missing):
        raise RuntimeError(existing.stderr)
    subprocess.run(['docker', 'tag', local, remote], check=True)
    subprocess.run(['docker', 'push', remote], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('local')
    parser.add_argument('remote')
    args = parser.parse_args()
    push(args.local, args.remote)


if __name__ == '__main__':
    main()
