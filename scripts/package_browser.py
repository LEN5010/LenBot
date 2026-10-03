"""Package already built browser helpers, the extension and actual dependency metadata."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib

PROJECT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--platform', required=True, choices=('linux-amd64', 'linux-arm64', 'macos-amd64', 'macos-arm64'))
    args = parser.parse_args()
    version = tomllib.loads((PROJECT / 'pyproject.toml').read_text())['project']['version']
    component = json.loads((PROJECT / 'deploy/components.json').read_text())['browserskill']
    name = f'browserskill-{version}-{args.platform}'
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        bundle = Path(temporary) / name
        (bundle / 'bin').mkdir(parents=True)
        for binary in ('bsk', 'bsk-file-host'):
            shutil.copy2(args.source / 'target/release' / binary, bundle / 'bin' / binary)
        shutil.copytree(args.source / 'apps/extension/dist/chrome-mv3', bundle / 'extension')
        for file in ('LICENSE', 'Cargo.lock', 'pnpm-lock.yaml'):
            shutil.copy2(args.source / file, bundle / file)
        shutil.copy2(PROJECT / 'deploy/browser/README.md', bundle / 'README.md')
        (bundle / 'source-docs').mkdir()
        shutil.copy2(args.source / 'docs/remote-extension-connection.md',
                     bundle / 'source-docs/remote-extension-connection.md')
        shutil.copy2(args.source / 'crates/bsk-file-host/README.md', bundle / 'source-docs/file-host.md')
        shutil.copy2(PROJECT / component['patch'], bundle / 'browserskill-remote-files.patch')
        (bundle / 'component.json').write_text(json.dumps({**component, 'lenbot_version': version,
            'platform': args.platform}, ensure_ascii=False, indent=2) + '\n')
        cargo = subprocess.check_output(['cargo', '+' + component['rust'], 'metadata', '--locked', '--format-version', '1'], cwd=args.source)
        (bundle / 'cargo-metadata.json').write_bytes(cargo)
        # Retain the originals available in the dependencies resolved by this build.
        for package in json.loads(cargo)['packages']:
            directory = Path(package['manifest_path']).parent
            notices = [p for p in directory.iterdir() if p.is_file()
                       and p.name.lower().split('.')[0].split('-')[0] in ('license', 'licence', 'copying', 'notice', 'copyright')]
            if package.get('license_file') is not None:
                notices.append(directory / package['license_file'])
            destination = bundle / 'licenses/rust' / (package['name'] + '-' + package['version'])
            destination.mkdir(parents=True, exist_ok=True)
            for source in set(notices):
                shutil.copy2(source, destination / source.name)
        with (bundle / 'npm-licenses.json').open('wb') as stream:
            subprocess.run(['pnpm', 'licenses', 'list', '--json'], cwd=args.source, stdout=stream, check=True)
        subprocess.run(['node', str(PROJECT / 'scripts/collect_frontend_licenses.cjs'),
            str(args.source / 'apps/extension/node_modules'), str(bundle / 'licenses/extension')], check=True)
        output = args.output / (name + '.tar.gz')
        with tarfile.open(output, 'x:gz') as archive:
            archive.add(bundle, arcname=name)
        print(output)


if __name__ == '__main__':
    main()
