"""Install declared plugin packages into a new host environment without changing plugin source."""

import asyncio
from pathlib import Path

from ..config import load_host_config
from ..instance_lock import instance_lock
from ..plugins.install import PluginInstaller, install_dependencies
from ..plugins.manifest import discover, read_manifest


async def install(root: Path) -> None:
    config = load_host_config(root)
    if config.plugins is None:
        print('没有已配置插件，Python 环境保持。')
        return
    found, errors = discover(config.plugins.paths)
    if errors:
        raise ValueError('\n'.join(errors))
    manifests = []
    installer = PluginInstaller(root)
    for name in config.plugins.configured:
        if (installer.records / (name + '.json')).is_file() and installer.read(name).installed is None:
            continue
        directories = found.get(name, [])
        if len(directories) != 1:
            raise ValueError(f'插件 {name} 需要唯一源码目录，实际为：{directories}')
        try:
            manifests.append(read_manifest(directories[0]))
        except ValueError as error:
            if name not in config.plugins.disabled:
                raise
            print(f'停用插件 {name} 不兼容，保留源码和数据，不恢复依赖：{error}')
    requirements = list(dict.fromkeys(item for manifest in manifests for item in manifest.dependencies))
    print('已配置插件：' + ', '.join(item.name for item in manifests))
    if requirements:
        print(await install_dependencies(requirements))
    else:
        print('没有声明额外依赖，Python 环境保持。')


def main() -> None:
    root = Path.cwd()
    with instance_lock(root):
        asyncio.run(install(root))


if __name__ == '__main__':
    main()
