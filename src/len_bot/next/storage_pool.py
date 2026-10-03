"""Read an actual ext4 filesystem limit or APFS volume quota; never a configured size estimate."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from xml.parsers.expat import ExpatError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .storage_pool_config import APFSPool, Ext4Pool, StoragePool
from .tasks_config import WorkerSettings


class CommandRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')


class MountRecord(CommandRecord):
    target: str
    source: str
    fstype: str
    fsroot: str


class MountList(CommandRecord):
    filesystems: list[MountRecord]


class APFSVolume(CommandRecord):
    APFSVolumeUUID: str
    CapacityQuota: int = Field(ge=0)
    CapacityInUse: int = Field(ge=0)


class APFSContainer(CommandRecord):
    CapacityFree: int = Field(ge=0)
    Volumes: list[APFSVolume]


class APFSList(CommandRecord):
    Containers: list[APFSContainer]


class APFSInfo(CommandRecord):
    VolumeUUID: str
    MountPoint: str
    FilesystemType: str


@dataclass(frozen=True)
class PoolUsage:
    kind: str
    mount: str
    source: str
    limit_bytes: int
    used_bytes: int
    available_bytes: int
    scope: str = 'instance-task-pool'


def command_output(*argv: str, timeout: float = 30) -> bytes:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f'Storage command timed out: {error}; stdout={error.stdout!r}; stderr={error.stderr!r}') from error
    if result.returncode:
        raise RuntimeError(f'{argv!r} exited {result.returncode}: '
                           f'{result.stderr.decode("utf-8", errors="replace")}\n'
                           f'{result.stdout.decode("utf-8", errors="replace")}')
    return result.stdout


def read_plist[T: BaseModel](data: bytes, schema: type[T]) -> T:
    try:
        return schema.model_validate(plistlib.loads(data))
    except (plistlib.InvalidFileException, ExpatError, ValueError) as error:
        raise ValueError(f'Invalid diskutil response: {error}; raw={data[:700]!r}') from error


def apfs_usage(pool: APFSPool, *, timeout: float) -> PoolUsage:
    info = read_plist(command_output('/usr/sbin/diskutil', 'info', '-plist', pool.volume_uuid, timeout=timeout), APFSInfo)
    if (info.FilesystemType != 'apfs' or Path(info.MountPoint) != pool.mount
            or info.VolumeUUID.casefold() != pool.volume_uuid.casefold()):
        raise ValueError(f'Configured APFS pool is not mounted at {pool.mount}: {info.model_dump()!r}')
    listing = read_plist(command_output('/usr/sbin/diskutil', 'apfs', 'list', '-plist', timeout=timeout), APFSList)
    selected = [(container, volume) for container in listing.Containers for volume in container.Volumes
                if volume.APFSVolumeUUID.casefold() == pool.volume_uuid.casefold()]
    if len(selected) != 1:
        raise ValueError(f'APFS listing does not identify one configured volume: {pool.volume_uuid}')
    container, volume = selected[0]
    if volume.CapacityQuota == 0:
        raise ValueError(f'APFS pool has no volume quota: {pool.volume_uuid}')
    filesystem = os.statvfs(pool.mount)
    available = min(volume.CapacityQuota - volume.CapacityInUse, container.CapacityFree,
                    filesystem.f_bavail * filesystem.f_frsize)
    return PoolUsage('apfs', str(pool.mount), pool.volume_uuid, volume.CapacityQuota,
                     volume.CapacityInUse, max(0, available))


def ext4_usage(pool: Ext4Pool, *, timeout: float) -> PoolUsage:
    raw = command_output('findmnt', '--json', '--mountpoint', str(pool.mount),
                         '--output', 'SOURCE,TARGET,FSTYPE,FSROOT', timeout=timeout)
    try:
        listing = MountList.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(f'Invalid findmnt response: {error}; raw={raw[:700]!r}') from error
    if len(listing.filesystems) != 1:
        raise ValueError(f'Expected one task pool mount: {raw[:700]!r}')
    mounted = listing.filesystems[0]
    if mounted.fstype != 'ext4' or mounted.fsroot != '/' or Path(mounted.target) != pool.mount:
        raise ValueError(f'Task pool must mount a whole ext4 filesystem: {mounted.model_dump()!r}')
    usage = os.statvfs(pool.mount)
    return PoolUsage('ext4', str(pool.mount), mounted.source, usage.f_blocks * usage.f_frsize,
                     (usage.f_blocks - usage.f_bfree) * usage.f_frsize, usage.f_bavail * usage.f_frsize)


def inspect_pool(pool: StoragePool, *, timeout: float = 30) -> PoolUsage:
    if not os.path.ismount(pool.mount):
        raise ValueError(f'Task storage pool is not mounted: {pool.mount}')
    if pool.kind == 'apfs':
        if sys.platform != 'darwin':
            raise ValueError('An APFS pool is read by the native macOS host; Linux hosts use an ext4 pool')
        return apfs_usage(pool, timeout=timeout)
    if sys.platform != 'linux':
        raise ValueError('An ext4 pool requires a Linux host')
    return ext4_usage(pool, timeout=timeout)


def worker_pool_usage(settings: WorkerSettings) -> dict | None:
    pool = settings.storage_pool
    if pool is None:
        return None
    result = inspect_pool(pool, timeout=settings.command_timeout_seconds)
    device = pool.mount.stat().st_dev
    for path in (settings.workspace_root, settings.runtime_root):
        if path.stat().st_dev != device:
            raise ValueError(f'Task directory is not on its configured storage pool: {path}')
    return asdict(result)
