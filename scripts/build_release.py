"""Build corresponding source and wheel in a separate tree; never replace a running panel."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time
import zipfile

from build_deployment import build_deployments


REQUIRED_SOURCE = (
    'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'uv.lock', '.dockerignore',
    'scripts/install.sh', 'scripts/build_release.py', 'scripts/build_deployment.py',
    'deploy/package/install.sh', 'deploy/package/install.py', 'deploy/package/README.md',
    'deploy/components.json', 'deploy/release-notes.md', 'deploy/releasing.md',
    '.github/workflows/release.yml', 'scripts/prepare_component.py', 'scripts/package_browser.py',
    'scripts/collect_python_licenses.py',
    'scripts/collect_frontend_licenses.cjs', 'deploy/current/README.md',
    'deploy/current/lenbot.service', 'deploy/current/Dockerfile', 'deploy/current/memory-templates.md',
    'deploy/current/docker.md', 'deploy/current/host.compose.yaml', 'deploy/current/host.tasks.compose.yaml',
    'deploy/current/services.compose.yaml', 'deploy/current/Dockerfile.openviking',
    'deploy/current/start-asr.sh', 'deploy/current/asr.md',
    'deploy/current/memory-forget.md', 'deploy/current/openviking-forget.patch',
    'deploy/current/browserskill-files.md', 'deploy/current/browserskill-remote-files.patch',
    'deploy/README.md', 'deploy/current/operations.md', 'CONTRIBUTING.md',
    'docker/next-worker/Dockerfile', 'docker/next-worker/lenbot-extension.ts',
    'docker/next-worker/lenbot-browser.cjs', 'docker/next-worker/Dockerfile.dockerignore',
    'docker/next-worker/lenbot-render.cjs', 'docker/next-worker/browser-files.cjs',
)
FORBIDDEN_SOURCE = ('docs/', 'runtime/', '.runtime/', '.backups/', '.venv/', 'state/', 'personas/', 'file_assets/')


def check_local_imports(package: dict[str, bytes]) -> int:
    """Inspect literal package imports without importing business code or running the host."""
    modules = {name[:-3].replace('/', '.') for name in package if name.endswith('.py')}
    prefixes = {name.rsplit('.', 1)[0] for name in modules}
    checked = 0
    for name, data in package.items():
        if not name.endswith('.py'):
            continue
        scope = name.split('/')[:-1]
        for node in ast.walk(ast.parse(data, filename=name)):
            targets = []
            if isinstance(node, ast.Import):
                targets = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = scope[:len(scope) - node.level + 1]
                    target = '.'.join([*base, *(node.module.split('.') if node.module is not None else [])])
                else:
                    target = node.module
                if target is not None:
                    targets = [target]
            for target in targets:
                if target == 'len_bot' or target.startswith('len_bot.'):
                    checked += 1
                    if target not in modules and target + '.__init__' not in modules and target not in prefixes:
                        raise ValueError(f'Current wheel literal import refers to absent package code: {name}:{node.lineno}: {target}')
    return checked


def single(directory: Path, pattern: str) -> Path:
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f'Build must produce exactly one {pattern} in {directory}; actual={paths!r}')
    return paths[0]


def source_files(path: Path) -> dict[str, bytes]:
    with tarfile.open(path) as archive:
        entries = archive.getmembers()
        roots = {PurePosixPath(entry.name).parts[0] for entry in entries}
        if len(roots) != 1:
            raise ValueError(f'Source package must have one top-level directory: {path}; actual={roots!r}')
        files = {}
        for entry in entries:
            if not entry.isfile():
                continue
            name = entry.name.split('/', 1)[1]
            if name in files:
                raise ValueError(f'Source package contains a duplicate file: {name!r}')
            with archive.extractfile(entry) as stream:
                files[name] = stream.read()
        return files


def inspect_packages(artifacts: Path, stage: Path) -> dict:
    source, wheel = single(artifacts, '*.tar.gz'), single(artifacts, '*.whl')
    files = source_files(source)
    missing = [name for name in REQUIRED_SOURCE if name not in files]
    leaked = [name for name in files if name == 'lenbot.config.json'
              or PurePosixPath(name).name == '.lenbot-instance.lock' or name.startswith(FORBIDDEN_SOURCE)
              or '/node_modules/' in name or '/__pycache__/' in name or name.endswith('.pyc')]
    if missing or leaked:
        raise ValueError(f'Release source lacks required files or contains local data: missing={missing!r}, local={leaked!r}')
    panel = stage / 'src/len_bot/web/static/dist'
    built = {path.relative_to(panel).as_posix(): path.read_bytes() for path in panel.rglob('*') if path.is_file()}
    if 'index.html' not in built:
        raise ValueError('The staged frontend build has no index.html')
    notice_index = 'assets/licenses/frontend/index.json'
    if notice_index not in built:
        raise ValueError('The built panel must retain this npm installation\'s notice inventory')
    frontend_notices = json.loads(built[notice_index])
    packages = {item['installed_path']: item for item in frontend_notices['packages']}
    for name in ('vue', 'vuetify', '@mdi/js'):
        item = packages[name]
        if item['license'] is None or not item['notice_files']:
            raise ValueError(f'Panel dependency lacks its declared license or original notices: {name}')
        for notice in item['notice_files']:
            prefix = f'assets/licenses/frontend/packages/{name}/{notice}'
            if not any(path == prefix or path.startswith(prefix + '/') for path in built):
                raise ValueError(f'Panel notice inventory refers to missing original content: {prefix}')
    source_panel = {name.removeprefix('src/len_bot/web/static/dist/'): data for name, data in files.items()
                    if name.startswith('src/len_bot/web/static/dist/')}
    if source_panel != built:
        raise ValueError('Source package panel bytes differ from this staged build')
    with zipfile.ZipFile(wheel) as archive:
        members = [entry for entry in archive.infolist() if not entry.is_dir()]
        names = [entry.filename for entry in members]
        if len(names) != len(set(names)):
            raise ValueError('Wheel has duplicate regular file names')
        if any(name.startswith('len_bot/web/frontend/')
               or ('/node_modules/' in name and not name.startswith('len_bot/web/static/dist/assets/licenses/frontend/'))
               or '/__pycache__/' in name or name.endswith('.pyc')
               or PurePosixPath(name).name == '.lenbot-instance.lock' for name in names):
            raise ValueError('Wheel includes frontend build tools/source, Python bytecode or an instance lock')
        installed = {entry.filename.removeprefix('len_bot/web/static/dist/'): archive.read(entry)
                     for entry in members if entry.filename.startswith('len_bot/web/static/dist/')}
        if installed != built:
            raise ValueError('Wheel panel bytes differ from this staged build')
        expected_package = {name.removeprefix('src/'): data for name, data in files.items()
                            if name.startswith('src/len_bot/') and not name.startswith('src/len_bot/web/frontend/')}
        installed_package = {entry.filename: archive.read(entry) for entry in members
                             if entry.filename.startswith('len_bot/')}
        if installed_package != expected_package:
            raise ValueError('Wheel differs from current runtime source/resource bytes')
        literal_imports = check_local_imports(installed_package)
        notices = [name for name in names if '.dist-info/licenses/' in name]
        for expected in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
            paths = [name for name in notices if name.endswith('/' + expected)]
            if len(paths) != 1 or archive.read(paths[0]) != files[expected]:
                raise ValueError(f'Wheel must retain the exact source {expected}')
    return {'source': str(source), 'wheel': str(wheel), 'panel_files': len(built),
            'frontend_notice_packages': len(packages),
            'wheel_scope': 'current core, evaluation, prompts, builtin skills, shared image/auth/shell and built panel',
            'literal_local_imports_checked': literal_imports,
            'package_files': len(expected_package), 'current_source_and_wheel_package_bytes_match': True,
            'source_and_wheel_panel_bytes_match_build': True,
            'required_source_files': list(REQUIRED_SOURCE), 'wheel_notice_files': notices,
            'notice': 'Packaging inspection only; no installation, image, service, model, or platform verification.'}


def build(project: Path, output: Path, *, offline: bool, npm_cache: Path | None) -> dict:
    project, output = project.resolve(), output.resolve()
    if output.is_relative_to(project) or project.is_relative_to(output):
        raise ValueError('Release output must be a separate directory outside the source tree')
    if output.exists():
        raise FileExistsError(f'Release output is never overwritten: {output}')
    output.mkdir(parents=True, mode=0o700)
    logs = output / 'logs'
    logs.mkdir()
    # This controls dependency-build caches only, never application configuration.
    environment = dict(os.environ)
    environment.setdefault('UV_CACHE_DIR', str(output / 'uv-cache'))
    npm_cache = output / 'npm-cache' if npm_cache is None else npm_cache.expanduser().resolve()
    report = {'project': str(project), 'output': str(output), 'started': time.time(),
              'finished': None, 'commands': [], 'artifacts': None, 'error': None}

    def run(name: str, command: list[str], cwd: Path) -> None:
        log = logs / (name + '.log')
        record = {'command': command, 'cwd': str(cwd), 'log': str(log), 'started': time.time(),
                  'finished': None, 'returncode': None}
        report['commands'].append(record)
        with log.open('x') as stream:
            process = subprocess.run(command, cwd=cwd, env=environment, stdout=stream, stderr=subprocess.STDOUT)
        record.update(finished=time.time(), returncode=process.returncode)
        if process.returncode:
            raise RuntimeError(f'{name} failed with exit {process.returncode}; raw output at {log}:\n'
                               + log.read_text()[-4000:])

    try:
        raw = output / 'initial-source'
        raw.mkdir()
        options = ['--offline'] if offline else []
        run('source', ['uv', 'build', '--sdist', *options, '--out-dir', str(raw)], project)
        source = single(raw, '*.tar.gz')
        stage_root = output / 'stage'
        stage_root.mkdir()
        with tarfile.open(source) as archive:
            roots = {PurePosixPath(entry.name).parts[0] for entry in archive.getmembers()}
            if len(roots) != 1:
                raise ValueError(f'Initial source archive has multiple top-level directories: {roots!r}')
            archive.extractall(stage_root, filter='data')
        stage = stage_root / next(iter(roots))
        frontend = stage / 'src/len_bot/web/frontend'
        panel = stage / 'src/len_bot/web/static/dist'
        if panel.exists():
            shutil.rmtree(panel)
        npm_options = ['--offline'] if offline else []
        run('frontend-install', ['npm', 'ci', '--no-audit', '--no-fund', '--cache', str(npm_cache), *npm_options], frontend)
        run('frontend-build', ['npm', 'run', 'build'], frontend)
        artifacts = output / 'artifacts'
        artifacts.mkdir()
        run('packages', ['uv', 'build', *options, '--out-dir', str(artifacts)], stage)
        report['artifacts'] = inspect_packages(artifacts, stage)
        report['artifacts']['deployment_bundles'] = [str(path) for path in
            build_deployments(Path(report['artifacts']['wheel']), stage, artifacts)]
        report['finished'] = time.time()
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New output directory outside this checkout; never overwritten')
    parser.add_argument('--offline', action='store_true', help='Require build dependency caches; never retry online')
    parser.add_argument('--npm-cache', type=Path, help='Explicit existing dependency cache; defaults to new output/npm-cache')
    args = parser.parse_args()
    result = build(Path(__file__).resolve().parents[1], args.output, offline=args.offline, npm_cache=args.npm_cache)
    print(json.dumps(result['artifacts'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
