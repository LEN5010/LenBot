"""Publish existing-tag assets without replacing an already uploaded file."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.artifacts / 'release-manifest.json').read_text(encoding='utf-8'))
    tag = manifest['tag']
    repository = os.environ['GITHUB_REPOSITORY']
    pages = subprocess.run(['gh', 'api', '--paginate', '--slurp', f'repos/{repository}/releases'],
                           capture_output=True, text=True, check=True)
    matching = [release for page in json.loads(pages.stdout) for release in page if release['tag_name'] == tag]
    if matching:
        release, = matching
        if release['prerelease'] != manifest['prerelease'] or release['draft']:
            raise ValueError('Existing Release channel differs; do not rewrite it')
    else:
        command = ['gh', 'release', 'create', tag, '--verify-tag', '--title', 'LenBot ' + manifest['version'],
                   '--notes-file', str(args.artifacts / 'release-notes.md')]
        if manifest['prerelease']:
            command.append('--prerelease')
        subprocess.run(command, check=True)
        release = {'assets': []}
    assets = {item['name']: item for item in release['assets']}
    files = [args.artifacts / 'release-manifest.json', *sorted(
        path for path in args.artifacts.iterdir() if path.is_file() and path.name != 'release-manifest.json')]
    for path in files:
        if path.name in assets:
            expected = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
            if assets[path.name]['digest'] != expected:
                raise ValueError(f'Existing Release asset differs: {path.name}; publish a new version')
            print(f'Retaining matching Release asset: {path.name}')
        else:
            subprocess.run(['gh', 'release', 'upload', tag, str(path)], check=True)


if __name__ == '__main__':
    main()
