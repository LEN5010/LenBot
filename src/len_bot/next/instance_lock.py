"""One current command owns an instance root until all its resources close."""

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import sys

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


class InstanceBusyError(RuntimeError):
    """The OS did not grant this command ownership of its instance file."""


@contextmanager
def instance_lock(root: Path) -> Iterator[None]:
    if root.resolve(strict=True) != root:
        raise ValueError(f'Instance command root must not traverse a symbolic link: {root}')
    path = root / '.lenbot-instance.lock'
    if path.is_symlink():
        raise ValueError(f'Instance lock must not be a symbolic link: {path}')
    flags = os.O_RDWR | os.O_CREAT
    if sys.platform != 'win32':
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Instance lock must be a regular file: {path}; mode={oct(info.st_mode)}')
        try:
            if sys.platform == 'win32':
                if info.st_size == 0:
                    os.write(descriptor, b'\0')
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise InstanceBusyError(f'Instance lock is occupied: {path}; stop its current runtime or '
                                    f'maintenance command before starting another; {type(error).__name__}: {error}') from error
        yield
    finally:
        os.close(descriptor)
