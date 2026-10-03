"""Render operating-system mount and container bindings from the sole root config."""

import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .config import HostConfig


def fstab_path(path: Path) -> str:
    return str(path).replace('\\', r'\134').replace(' ', r'\040').replace('\t', r'\011').replace('\n', r'\012')


def deployment(config: HostConfig, root: Path) -> dict:
    pool = config.worker.storage_pool
    if pool.kind == 'apfs':
        return {'host': 'native-macos', 'docker_desktop_shared_path': str(pool.mount),
                'working_directory': str(root),
                'mount_command': [sys.executable, '-m', 'len_bot.next.storage_pool_admin', 'mount']}
    socket = Path(urlsplit(config.worker.docker_host).path)
    owner = (root / 'lenbot.config.json').stat()
    quoted = json.dumps(str(pool.mount).replace('%', '%%'), ensure_ascii=False)
    volumes = [{'type': 'bind', 'source': str(path), 'target': str(path), 'bind': {'create_host_path': False}}
               for path in (root, pool.mount, socket)]
    return {'host': 'linux',
            'fstab_line': f'{fstab_path(pool.image)} {fstab_path(pool.mount)} ext4 loop,nodev,nosuid,noauto 0 0',
            'systemd_dropin': f'[Unit]\nRequiresMountsFor={quoted}\n\n[Service]\nReadWritePaths={quoted}\n',
            'compose_override': {'services': {'lenbot': {
                'working_dir': str(root),
                'user': f'{owner.st_uid}:{owner.st_gid}', 'group_add': [str(socket.stat().st_gid)],
                'volumes': volumes}}},
            'docker_cli_path': str(config.worker.docker_binary)}
