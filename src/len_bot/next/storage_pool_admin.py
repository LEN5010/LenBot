"""Explicit filesystem provisioning; runtime settings remain in lenbot.config.json."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from pydantic import Field, ValidationError

from .config import load_host_config
from .instance_lock import instance_lock
from .storage_pool import APFSInfo, CommandRecord, command_output, inspect_pool, read_plist
from .storage_pool_config import APFSPool, Ext4Pool, StoragePool


class LoopDevice(CommandRecord):
    name: str
    backing_file: str = Field(alias='back-file')


class LoopDevices(CommandRecord):
    loopdevices: list[LoopDevice]


def admin_command(*argv: str) -> bytes:
    command = argv if os.geteuid() == 0 else ('sudo', '--', *argv)
    return command_output(*command, timeout=300)


def empty_mount(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.path.ismount(path) or any(path.iterdir()):
        raise ValueError(f'New pool requires an empty, unmounted directory: {path}')


def create_directories(pool: StoragePool, uid: int, gid: int) -> None:
    for name in ('workspaces', 'runtime'):
        admin_command('install', '-d', '-m', '0700', '-o', str(uid), '-g', str(gid), str(pool.mount / name))


def create_ext4(image: Path, mount: Path, size: int, uid: int, gid: int) -> Ext4Pool:
    if sys.platform != 'linux':
        raise ValueError('ext4 image creation runs on the Linux Docker host')
    pool = Ext4Pool(kind='ext4', image=image, mount=mount)
    empty_mount(mount)
    with image.open('xb') as stream:
        stream.truncate(size)
    command_output('mkfs.ext4', '-F', '-m', '0', str(image), timeout=300)
    admin_command('mount', '-t', 'ext4', '-o', 'loop,nodev,nosuid', str(image), str(mount))
    create_directories(pool, uid, gid)
    return pool


def create_apfs(container: str, name: str, mount: Path, size: int, uid: int, gid: int) -> APFSPool:
    if sys.platform != 'darwin':
        raise ValueError('APFS volume creation runs on the native macOS host')
    empty_mount(mount)
    admin_command('/usr/sbin/diskutil', 'apfs', 'addVolume', container, 'Case-sensitive APFS', name,
                  '-quota', f'{size}B', '-mountpoint', str(mount))
    info = read_plist(command_output('/usr/sbin/diskutil', 'info', '-plist', str(mount)), APFSInfo)
    pool = APFSPool(kind='apfs', volume_uuid=info.VolumeUUID, mount=mount)
    create_directories(pool, uid, gid)
    return pool


def mount_pool(pool: StoragePool) -> None:
    empty_mount(pool.mount)
    if pool.kind == 'ext4':
        if sys.platform != 'linux':
            raise ValueError('Mount the ext4 pool on the Linux Docker host')
        admin_command('mount', '-t', 'ext4', '-o', 'loop,nodev,nosuid', str(pool.image), str(pool.mount))
    else:
        if sys.platform != 'darwin':
            raise ValueError('Mount the APFS pool on the native macOS host')
        admin_command('/usr/sbin/diskutil', 'mount', '-mountPoint', str(pool.mount), pool.volume_uuid)


def grow_ext4(pool: Ext4Pool, size: int) -> None:
    current = inspect_pool(pool)
    if size < pool.image.stat().st_size:
        raise ValueError('Pool expansion does not shrink the image')
    raw = command_output('losetup', '--json', '--list', '--associated', str(pool.image), '--output', 'NAME,BACK-FILE')
    try:
        devices = LoopDevices.model_validate_json(raw).loopdevices
    except ValidationError as error:
        raise ValueError(f'Invalid losetup response: {error}; raw={raw[:700]!r}') from error
    if (len(devices) != 1 or devices[0].name != current.source
            or Path(devices[0].backing_file) != pool.image):
        raise ValueError(f'Pool mount does not use the configured image: {raw[:700]!r}')
    with pool.image.open('r+b') as stream:
        stream.truncate(size)
    admin_command('losetup', '--set-capacity', devices[0].name)
    admin_command('resize2fs', devices[0].name)


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('value must be positive')
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Create a task storage pool, inspect it, or explicitly mount/expand it while the instance is stopped.')
    actions = result.add_subparsers(dest='action', required=True)
    for kind in ('ext4', 'apfs'):
        create = actions.add_parser(f'create-{kind}', help='Create a new pool and print its root-config fields; no existing files or config are moved.')
        create.add_argument('--mount', type=Path, required=True)
        create.add_argument('--size-gib', type=positive, required=True)
        create.add_argument('--uid', type=positive, required=True)
        create.add_argument('--gid', type=positive, required=True)
        if kind == 'ext4':
            create.add_argument('--image', type=Path, required=True)
        else:
            create.add_argument('--container', required=True, help='Existing APFS container device, for example disk3')
            create.add_argument('--name', required=True)
    actions.add_parser('status', help='Read the pool named in the current instance root config.')
    actions.add_parser('mount', help='Mount the configured pool before starting this instance.')
    grow = actions.add_parser('grow', help='Expand the configured ext4 image. For APFS, create a larger-quota pool and move data offline.')
    grow.add_argument('--size-gib', type=positive, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.action.startswith('create-'):
        mount = args.mount.expanduser().resolve()
        if args.action == 'create-ext4':
            pool = create_ext4(args.image.expanduser().resolve(), mount, args.size_gib * 1024**3, args.uid, args.gid)
        else:
            pool = create_apfs(args.container, args.name, mount, args.size_gib * 1024**3, args.uid, args.gid)
        print(json.dumps({'storage_pool': pool.model_dump(mode='json'),
            'workspace_root': str(pool.mount / 'workspaces'), 'runtime_root': str(pool.mount / 'runtime'),
            'usage': asdict(inspect_pool(pool))}, ensure_ascii=False, indent=2))
        return
    root = Path.cwd().resolve()
    config = load_host_config(root)
    if config.worker is None or config.worker.storage_pool is None:
        raise ValueError('Current root config has no worker.storage_pool')
    pool = config.worker.storage_pool
    if args.action == 'status':
        print(json.dumps(asdict(inspect_pool(pool)), ensure_ascii=False, indent=2))
        return
    with instance_lock(root):
        if args.action == 'mount':
            mount_pool(pool)
        elif args.action == 'grow':
            if pool.kind != 'ext4':
                raise ValueError('APFS expansion uses a new larger-quota volume and an explicit offline data move')
            grow_ext4(pool, args.size_gib * 1024**3)
        print(json.dumps(asdict(inspect_pool(pool)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
