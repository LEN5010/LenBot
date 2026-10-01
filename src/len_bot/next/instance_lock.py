"""One current command owns an instance root until all its resources close."""

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import stat


class InstanceBusyError(RuntimeError):
    """The OS did not grant this command ownership of its instance file."""


@contextmanager
def instance_lock(root: Path) -> Iterator[None]:
    if root.resolve(strict=True) != root:
        raise ValueError(f'Instance command root must not traverse a symbolic link: {root}')
    path = root / '.lenbot-instance.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Instance lock must be a regular file: {path}; mode={oct(info.st_mode)}')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise InstanceBusyError(f'Instance lock is occupied: {path}; stop its current runtime or '
                                    f'maintenance command before starting another; {type(error).__name__}: {error}') from error
        yield
    finally:
        os.close(descriptor)
