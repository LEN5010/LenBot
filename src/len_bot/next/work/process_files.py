"""Regular stderr files for task subprocesses."""

import os
from pathlib import Path
import stat


def open_stderr(path: Path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"Task stderr must be a regular file: {path}")
        os.set_blocking(descriptor, True)
        return os.fdopen(descriptor, "ab")
    except BaseException:
        os.close(descriptor)
        raise
