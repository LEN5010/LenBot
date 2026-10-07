"""Apply explicitly requested plugin candidates while the instance is stopped."""

import asyncio
from pathlib import Path
import sys

from ..config import load_instance_config
from ..instance_lock import instance_lock
from ..plugins.install import PluginInstaller, install_dependencies
from ..plugins.manifest import discover, read_manifest
from ..runtime.logs import run_maintenance


async def apply(root: Path) -> None:
    installer = PluginInstaller(root)
    records = [item for item in installer.pending() if item.requested]
    if not records:
        return
    config = load_instance_config(root)
    manifests = {}
    for record in records:
        try:
            manifests[record.name] = await installer.check_apply(record.name, config.plugins.configured[record.name], config.scenes)
        except Exception as error:
            installer.failed(record.name, error)
            raise
    found, _ = discover(config.plugins.paths)
    requirements = set()
    for name in config.plugins.configured:
        if name in manifests:
            manifest = manifests[name]
        elif name in config.plugins.disabled:
            continue
        else:
            directories = found.get(name, [])
            if len(directories) != 1:
                raise ValueError(f'Plugin {name} does not have one source directory: {directories!r}')
            manifest = read_manifest(directories[0])
        requirements.update(manifest.dependencies)
    try:
        output = await install_dependencies(sorted(requirements))
    except Exception as error:
        for record in records:
            installer.failed(record.name, error)
        raise
    if output:
        print(output, flush=True)
    for record in records:
        try:
            installer.apply_files(record.name)
        except Exception as error:
            installer.failed(record.name, error)
            raise
        print(f'{record.name}: applied {record.candidate.version}', flush=True)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Plugin application takes no arguments; use the instance root configuration')
    root = Path.cwd()
    with instance_lock(root):
        asyncio.run(apply(root))


if __name__ == '__main__':
    run_maintenance(main, 'apply_plugins')
