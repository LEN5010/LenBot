"""Explicit Git install/update and declared dependencies in the host environment."""
from __future__ import annotations

import asyncio
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from .plugin_manifest import Manifest, discover, parse_manifest, read_manifest


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
    if url.scheme not in {'http', 'https', 'ssh'} or not url.hostname or not url.path:
        raise ValueError('使用完整的 HTTP(S) 或 ssh:// 仓库 URL；不支持 ZIP、本地路径或快捷 SSH 写法')
    if url.password is not None or url.query or url.fragment or (url.scheme != 'ssh' and url.username is not None):
        raise ValueError('仓库 URL 不包含密码、查询串或片段；认证使用本机 Git 凭据配置')
    return value


class PluginInstaller:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / 'plugins'

    def managed_path(self, name: str) -> Path:
        path = self.directory / name
        if path.resolve() != path or not (path / '.git').is_dir():
            raise ValueError(f'{name} 不是安装器管理的独立 Git 插件目录：{path}')
        return path

    async def dependencies(self, manifest: Manifest) -> str:
        if not manifest.dependencies:
            return ''
        # Installing a plugin must not silently replace the running host's packages.
        with tempfile.TemporaryDirectory(prefix='lenbot-plugin-deps-') as temporary:
            constraints = Path(temporary) / 'installed.txt'
            constraints.write_text('\n'.join(sorted(
                f"{item.metadata['Name']}=={item.version}" for item in distributions())) + '\n')
            return await run_command('uv', 'pip', 'install', '--python', sys.executable,
                                     '--constraints', str(constraints), '--', *manifest.dependencies)

    async def install(self, url: str, paths: list[Path]) -> tuple[Path, Manifest, str]:
        url = repository_url(url)
        if self.directory.resolve() != self.directory:
            raise ValueError(f'插件安装目录不能穿过目录链接：{self.directory}')
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.install-', dir=self.directory) as temporary:
            checkout = Path(temporary) / 'checkout'
            output = await run_command('git', 'clone', '--', url, str(checkout))
            manifest = parse_manifest(checkout / 'plugin.toml')
            found, _ = discover(paths)
            destination = self.directory / manifest.name
            if manifest.name in found or destination.exists():
                raise ValueError(f'插件 {manifest.name} 已存在，使用更新而不是覆盖安装')
            # Keep a failed dependency installation visible and repairable in the plugin page.
            checkout.rename(destination)
        return destination, manifest, output.strip()

    async def update(self, name: str) -> tuple[Manifest, str]:
        path = self.managed_path(name)
        if await run_command('git', 'status', '--porcelain', '--untracked-files=no', cwd=path):
            raise ValueError(f'插件 {name} 有本地修改，未覆盖；先由维护者处理后再更新')
        output = await run_command('git', 'pull', '--ff-only', cwd=path)
        manifest = read_manifest(path)
        output += '\n' + await self.dependencies(manifest)
        return manifest, output.strip()

    async def uninstall(self, name: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self.managed_path(name))
