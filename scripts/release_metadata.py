"""The release version and downloadable artifact contract."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

SHIPPED = ('src', 'deploy', 'docker', 'scripts', 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE', 'NOTICE',
           'THIRD_PARTY_NOTICES.md')
VERSION = re.compile(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?')


def release_version(value: str) -> str:
    if VERSION.fullmatch(value) is None:
        raise ValueError(f'Expected X.Y.Z, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN: {value!r}')
    return value


def constant(project: Path, module: str, name: str) -> int:
    tree = ast.parse((project / 'src/len_bot/next' / module).read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f'{module} has no literal {name}')


def source_revision(project: Path) -> str:
    """HEAD, marked -dirty when the working tree differs, so a local candidate never passes for that commit."""
    git = lambda *args: subprocess.run(['git', *args], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
    # Only shipped sources count; downloaded artifacts in the checkout root do not make a CI build dirty.
    changed = git('status', '--porcelain', '--untracked-files=normal', '--', *SHIPPED)
    return git('rev-parse', 'HEAD') + ('-dirty' if changed else '')


def write_manifest(project: Path, artifacts: Path, *, revision: str, images: dict | None = None) -> Path:
    version = release_version(tomllib.loads((project / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version'])
    files = {}
    for path in sorted(artifacts.iterdir()):
        if path.is_file() and path.name != 'release-manifest.json':
            files[path.name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    manifest = {
        'manifest_version': 1, 'version': version, 'tag': 'v' + version, 'revision': revision,
        'prerelease': re.search(r'(a|b|rc)\d+$', version) is not None,
        'python': '3.13', 'updater_protocol': 1,
        'plugin_interface': constant(project, 'plugin.py', 'INTERFACE'),
        'formats': {'config': constant(project, 'config.py', 'CONFIG_VERSION'),
                    'business': constant(project, 'storage/store.py', 'FORMAT_VERSION'),
                    'memory_jobs': constant(project, 'memory/jobs.py', 'FORMAT_VERSION'),
                    'local_memory': constant(project, 'memory/local.py', 'FORMAT_VERSION')},
        'bundles': {platform: f'lenbot-{version}-{platform}' + ('.zip' if platform == 'windows' else '.tar.gz')
                    for platform in ('linux', 'macos', 'windows')},
        'files': files, 'images': {} if images is None else images,
    }
    target = artifacts / 'release-manifest.json'
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('--images', type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    revision = source_revision(project)
    if args.images is not None and revision.endswith('-dirty'):
        raise SystemExit('Published manifests must come from a clean checkout of the tagged commit')
    images = None if args.images is None else json.loads(args.images.read_text(encoding='utf-8'))
    print(write_manifest(project, args.artifacts, revision=revision, images=images))


if __name__ == '__main__':
    main()
