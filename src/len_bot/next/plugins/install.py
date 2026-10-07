"""Explicit Git install/update and declared dependencies in the host environment."""
from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from importlib.metadata import distributions
import hashlib
import os
import stat
import zipfile
from io import BytesIO
from typing import Literal
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from .manifest import Manifest, discover, parse_manifest, read_manifest
from ..work.materials import finish_file_operation

MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_ZIP_FILES = 4096


async def run_command(*args: str, cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(*args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
        await process.wait()
        raise
    try:
        text = output.decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'{args[0]} 输出不是 UTF-8：{error}；原始片段：{output[:300]!r}') from error
    if process.returncode != 0:
        raise RuntimeError(f'{args[0]} 退出码 {process.returncode}：\n{text}')
    return text.strip()


def repository_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme not in {'https', 'ssh'} or not url.hostname or not url.path:
        raise ValueError('使用完整的 https:// 或 ssh:// 仓库 URL；不使用明文 HTTP、本地路径或快捷 SSH 写法')
    if url.password is not None or url.query or url.fragment or (url.scheme != 'ssh' and url.username is not None):
        raise ValueError('仓库 URL 不包含密码、查询串或片段；认证使用本机 Git 凭据配置')
    return value


def revision_ref(value: str) -> str:
    if not value.strip() or value.startswith(('-', '+')) or any(char in value for char in (':', '\x00', '\r', '\n')):
        raise ValueError('ref 使用分支、标签或提交，不使用 Git 选项或 refspec')
    return value


async def install_dependencies(requirements: Sequence[str]) -> str:
    if not requirements:
        return ''
    # Keep the host environment fixed while resolving the explicitly requested plugin packages.
    with tempfile.TemporaryDirectory(prefix='lenbot-plugin-deps-') as temporary:
        constraints = Path(temporary) / 'installed.txt'
        constraints.write_text('\n'.join(sorted(
            f"{item.metadata['Name']}=={item.version}" for item in distributions())) + '\n')
        return await run_command('uv', 'pip', 'install', '--python', sys.executable,
                                 '--constraints', str(constraints), '--', *requirements)


class Source(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['git', 'zip']
    location: str
    ref: str | None
    branch: str | None
    revision: str
    version: str


class Installation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str
    installed: Source | None
    candidate: Source | None
    application: Literal['plugin', 'host']
    requested: bool
    error: str | None
    # The source replaced by the last apply, kept in plugins/.previous/<name> for one rollback.
    previous: Source | None = None


class CandidateValues(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: str
    values: dict[str, object]
    enabled: bool


class PluginInstaller:
    def __init__(self, root: Path):
        self.root = root
        # Everything plugin-related stays under the instance's plugins/: installed
        # sources by name, and dot directories that discovery never treats as plugins.
        self.directory = root / 'plugins'
        self.records = self.directory / '.installations'
        self.candidates = self.directory / '.candidates'

    def candidate_values(self, name: str) -> CandidateValues | None:
        path = self.records / '.candidate-values' / (name + '.json')
        return CandidateValues.model_validate_json(path.read_bytes()) if path.exists() else None

    def _write_values(self, name: str, values: CandidateValues, *, previous: bool = False) -> None:
        directory = self.records / ('.previous-values' if previous else '.candidate-values')
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.values-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(values.model_dump_json(indent=2) + '\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / (name + '.json'))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def save_candidate_values(self, name: str, values: dict, enabled: bool) -> None:
        record = self.read(name)
        if record.candidate is None:
            raise ValueError(f'{name} has no prepared candidate')
        self._write_values(name, CandidateValues(revision=record.candidate.revision, values=values, enabled=enabled))

    def _publish_values(self, name: str, values: CandidateValues) -> None:
        from ..configuration.editing import _read_saved, save_config
        def edit(source, saved):
            plugins = source['plugins']
            plugins[name] = values.values
            disabled = [item for item in plugins.get('disabled', []) if item != name]
            plugins['disabled'] = disabled if values.enabled else [*disabled, name]
        save_config(self.root, _read_saved(self.root), edit)

    def publish_applied_values(self) -> None:
        for path in (self.records / '.candidate-values').glob('*.json'):
            values = CandidateValues.model_validate_json(path.read_bytes())
            record = self.read(path.stem)
            if record.candidate is None and record.installed is not None and record.installed.revision == values.revision:
                self._publish_values(record.name, values)
                path.unlink()

    def read(self, name: str) -> Installation:
        path = self.records / (name + '.json')
        raw = path.read_text(encoding='utf-8')
        try:
            return Installation.model_validate_json(raw)
        except ValidationError as error:
            raise ValueError(f'{path}: {error}; raw={raw[:500]!r}') from error

    def write(self, record: Installation) -> None:
        self.records.mkdir(parents=True, exist_ok=True)
        path = self.records / (record.name + '.json')
        descriptor, temporary = tempfile.mkstemp(prefix='.installation-', dir=self.records)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(record.model_dump_json(indent=2) + '\n')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def managed_path(self, name: str) -> Path:
        record = self.read(name)
        path = self.directory / name
        if record.installed is None or path.resolve() != path or not path.is_dir():
            raise ValueError(f'{name} has no installed managed source: {path}')
        return path

    def pending(self) -> list[Installation]:
        if not self.records.exists():
            return []
        return [record for path in sorted(self.records.glob('*.json'))
                if (record := self.read(path.stem)).candidate is not None]

    async def details(self, name: str) -> dict:
        record = self.read(name)
        source = record.installed
        return {**(source.model_dump() if source is not None else {}),
                'installed': None if source is None else source.model_dump(),
                'candidate': None if record.candidate is None else record.candidate.model_dump(),
                'previous': None if record.previous is None else record.previous.model_dump(),
                'application': record.application, 'requested': record.requested, 'error': record.error}

    async def checkout_ref(self, path: Path, ref: str) -> str:
        revision_ref(ref)
        await run_command('git', 'check-ref-format', '--allow-onelevel', ref)
        output = await run_command('git', 'fetch', '--no-tags', 'origin', ref, cwd=path)
        output += '\n' + await run_command('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=path)
        return output.strip()

    async def candidate(self, checkout: Path, source: Source, paths: list[Path], *, switch_source: bool) -> tuple[Manifest, Installation]:
        bytecode = await finish_file_operation(lambda: next((path for path in checkout.rglob('*')
            if path.name.casefold() == '__pycache__' or path.suffix.lower() == '.pyc'), None))
        if bytecode is not None:
            raise ValueError(f'Plugin candidate must contain source rather than Python bytecode: {bytecode}')
        manifest = parse_manifest(checkout / 'plugin.toml')
        manifest.require_compatible()
        if not (checkout / '__init__.py').is_file():
            raise ValueError(f'Plugin package lacks __init__.py: {checkout}')
        source.version = manifest.version
        name = manifest.name
        found, _ = discover(paths)
        managed = self.records / (name + '.json')
        record = (self.read(name) if managed.exists() else
                  Installation(name=name, installed=None, candidate=None, application='plugin', requested=False, error=None))
        destination = self.directory / name
        if record.installed is None and (name in found or destination.exists()):
            raise ValueError(f'{name} already belongs to a manual directory; source was not replaced')
        if record.installed is not None:
            previous = record.installed
            if not switch_source and (previous.kind != source.kind or
                                      (source.kind == 'git' and previous.location != source.location)):
                raise ValueError(f'{name} belongs to {previous.kind} {previous.location}; explicitly choose source replacement')
            old = read_manifest(self.managed_path(name))
            dependencies_changed = set(old.dependencies) != set(manifest.dependencies)
        else:
            dependencies_changed = bool(manifest.dependencies)
        record.application = 'host' if manifest.reload == 'host' or dependencies_changed else 'plugin'
        self.candidates.mkdir(parents=True, exist_ok=True)
        target = self.candidates / name
        if target.exists():
            await finish_file_operation(shutil.rmtree, target)
        checkout.rename(target)
        record.candidate, record.requested, record.error = source, False, None
        (self.records / '.candidate-values' / (name + '.json')).unlink(missing_ok=True)
        self.write(record)
        return manifest, record

    async def prepare_git(self, url: str, paths: list[Path], *, ref: str | None = None,
                          switch_source: bool = False) -> tuple[Manifest, Installation, str]:
        url = repository_url(url)
        self.candidates.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.git-', dir=self.candidates) as temporary:
            checkout = Path(temporary) / 'checkout'
            output = await run_command('git', 'clone', '--', url, str(checkout))
            if ref is not None:
                output += '\n' + await self.checkout_ref(checkout, ref)
            source = Source(kind='git', location=url, ref=ref,
                            branch=await run_command('git', 'branch', '--show-current', cwd=checkout),
                            revision=await run_command('git', 'rev-parse', 'HEAD', cwd=checkout), version='')
            manifest, record = await self.candidate(checkout, source, paths, switch_source=switch_source)
        return manifest, record, output

    @staticmethod
    def _unpack(data: bytes, checkout: Path) -> Path:
        checkout.mkdir()
        try:
            archive = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as error:
            raise ValueError(f'{error}; ZIP raw={data[:80]!r}') from error
        with archive:
            entries = archive.infolist()
            if len(data) > MAX_ZIP_BYTES or sum(item.file_size for item in entries) > MAX_ZIP_BYTES:
                raise ValueError('Plugin ZIP exceeds 200 MiB uploaded or extracted content')
            if len(entries) > MAX_ZIP_FILES:
                raise ValueError(f'Plugin ZIP exceeds {MAX_ZIP_FILES} entries')
            seen = set()
            for item in entries:
                relative = PurePosixPath(item.filename)
                if (relative.is_absolute() or PureWindowsPath(item.filename).drive or '..' in relative.parts
                        or any(':' in part for part in relative.parts) or '\\' in item.filename
                        or item.filename in seen or stat.S_ISLNK(item.external_attr >> 16)):
                    raise ValueError(f'Invalid plugin ZIP member: {item.filename!r}')
                if any(part.casefold() == '__pycache__' for part in relative.parts) or relative.suffix.lower() == '.pyc':
                    raise ValueError(f'Plugin ZIP must contain source rather than Python bytecode: {item.filename!r}')
                if '__MACOSX' in relative.parts or any(part.startswith('._') for part in relative.parts):
                    continue
                seen.add(item.filename)
                target = checkout.joinpath(*relative.parts)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('xb') as stream:
                        try:
                            stream.write(archive.read(item))
                        except zipfile.BadZipFile as error:
                            raise ValueError(f'{error}; member={item.filename!r}; ZIP raw={data[:80]!r}') from error
                    if item.create_system == 3:
                        target.chmod(stat.S_IMODE(item.external_attr >> 16))
        children = list(checkout.iterdir())
        return checkout if (checkout / 'plugin.toml').is_file() else (
            children[0] if len(children) == 1 and children[0].is_dir() else checkout)

    async def prepare_zip(self, data: bytes, filename: str, paths: list[Path], *,
                          switch_source: bool = False) -> tuple[Manifest, Installation, str]:
        self.candidates.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.zip-', dir=self.candidates))
        try:
            package = await finish_file_operation(self._unpack, data, temporary / 'checkout')
            source = Source(kind='zip', location=filename, ref=None, branch=None,
                            revision=await finish_file_operation(lambda: hashlib.sha256(data).hexdigest()), version='')
            manifest, record = await self.candidate(package, source, paths, switch_source=switch_source)
        finally:
            await finish_file_operation(shutil.rmtree, temporary)
        return manifest, record, ''

    async def prepare_update(self, name: str, paths: list[Path], *, ref: str | None = None) -> tuple[Manifest, Installation, str]:
        source = self.read(name).installed
        if source is None or source.kind != 'git':
            raise ValueError(f'{name} has no Git source; import its next ZIP explicitly')
        selected = (source.ref if source.ref is not None else source.branch) if ref is None else revision_ref(ref)
        return await self.prepare_git(source.location, paths, ref=selected)

    async def check_apply(self, name: str, values: dict, scenes: Collection[str]) -> Manifest:
        record = self.read(name)
        if record.candidate is None:
            raise ValueError(f'{name} has no prepared candidate')
        manifest = read_manifest(self.candidates / name)
        manifest.values_model(scenes).model_validate(values)
        if record.installed is not None:
            self.managed_path(name)
        elif (self.directory / name).exists():
            raise ValueError(f'{name} destination already exists; source was not replaced')
        if record.installed is not None and record.installed.kind == 'git':
            if await run_command('git', 'status', '--porcelain', '--untracked-files=all', '--', '.', ':(exclude)**/__pycache__/**', cwd=self.directory / name):
                raise ValueError(f'{name} has local source changes; candidate was not applied')
        return manifest

    def apply_files(self, name: str) -> None:
        """Install the candidate; the replaced source moves to plugins/.previous/<name> for one rollback."""
        record = self.read(name)
        values = self.candidate_values(name)
        destination = self.directory / name
        self.directory.mkdir(exist_ok=True)
        if destination.exists():
            from ..configuration.editing import _read_saved
            saved = _read_saved(self.root)
            self._write_values(name, CandidateValues(revision=record.installed.revision,
                values=saved.plugins.configured[name], enabled=name not in saved.plugins.disabled), previous=True)
            previous = self.directory / '.previous' / name
            if previous.exists():
                shutil.rmtree(previous)
            previous.parent.mkdir(exist_ok=True)
            destination.rename(previous)
            record.previous = record.installed
        (self.candidates / name).rename(destination)
        record.installed, record.candidate, record.requested, record.error = record.candidate, None, False, None
        self.write(record)
        if values is not None:
            self._publish_values(name, values)
            (self.records / '.candidate-values' / (name + '.json')).unlink()

    def rollback_files(self, name: str) -> None:
        """Put the source replaced by the last apply back; the plugin's data is not changed."""
        record = self.read(name)
        previous = self.directory / '.previous' / name
        if record.previous is None or not previous.is_dir():
            raise ValueError(f'{name} 没有保留上一版本源码')
        destination = self.directory / name
        if destination.exists():
            shutil.rmtree(destination)
        previous.rename(destination)
        record.installed, record.previous, record.error = record.previous, None, None
        self.write(record)
        (self.records / '.candidate-values' / (name + '.json')).unlink(missing_ok=True)
        values = self.records / '.previous-values' / (name + '.json')
        if values.exists():
            self._publish_values(name, CandidateValues.model_validate_json(values.read_bytes()))
            values.unlink()

    def failed(self, name: str, error: Exception) -> None:
        record = self.read(name)
        record.error = f'{type(error).__name__}: {error}'
        self.write(record)

    async def cancel(self, name: str) -> None:
        record = self.read(name)
        if record.candidate is None:
            raise ValueError(f'{name} has no prepared candidate')
        await asyncio.to_thread(shutil.rmtree, self.candidates / name)
        (self.records / '.candidate-values' / (name + '.json')).unlink(missing_ok=True)
        record.candidate, record.requested, record.error = None, False, None
        self.write(record)

    async def uninstall(self, name: str) -> None:
        record = self.read(name)
        if record.installed is not None:
            await asyncio.to_thread(shutil.rmtree, self.managed_path(name))
        if (self.directory / '.previous' / name).exists():
            await asyncio.to_thread(shutil.rmtree, self.directory / '.previous' / name)
        if record.candidate is not None:
            await asyncio.to_thread(shutil.rmtree, self.candidates / name)
        (self.records / (name + '.json')).unlink()
        for directory in ('.candidate-values', '.previous-values'):
            (self.records / directory / (name + '.json')).unlink(missing_ok=True)
