"""Read and manage files inside an explicitly selected skill directory."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat


PAGE_CHARACTERS = 4000


def _directory(root: Path) -> None:
    if root.resolve(strict=True) != root:
        raise ValueError(f"{root}: skill root must not traverse a symbolic link")
    mode = root.lstat().st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"{root}: skill root must be an actual directory, not a link or special file")


def list_files(root: Path, *, independent: bool = False) -> list[dict]:
    """List real regular files under a skill, without following any links."""
    _directory(root)
    files: list[dict] = []
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            children = list(entries)
        for child in children:
            relative = f"{prefix}{child.name}"
            details = child.stat(follow_symlinks=False)
            mode = details.st_mode
            if stat.S_ISDIR(mode):
                pending.append((Path(child.path), f"{relative}/"))
            elif stat.S_ISREG(mode):
                if independent and details.st_nlink != 1:
                    raise ValueError(f"{child.path}: move requires independent files, not shared hard links")
                files.append({"path": relative, "size": details.st_size})
            else:
                raise ValueError(f"{child.path}: skill contains a symbolic link or special file")
    return sorted(files, key=lambda item: item["path"])


def _regular_file(root: Path, path: str) -> Path:
    _directory(root)
    parts = path.split("/")
    if (not path or path.startswith("/") or "\\" in path
            or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(f"Invalid skill-relative path: {path!r}")
    target = root
    for index, part in enumerate(parts):
        target = target / part
        mode = target.lstat().st_mode
        if index == len(parts) - 1:
            if not stat.S_ISREG(mode):
                raise ValueError(f"{target}: skill file must be a real regular file")
        elif not stat.S_ISDIR(mode):
            raise ValueError(f"{target}: skill path component must be a real directory")
    return target


def read_file(root: Path, path: str, offset: int) -> dict:
    """Read one UTF-8 character page without materializing the whole file."""
    if offset < 0:
        raise ValueError(f"Skill file offset must be nonnegative: {offset}")
    target = _regular_file(root, path)
    try:
        with target.open("r", encoding="utf-8", newline="") as stream:
            remaining = offset
            while remaining:
                skipped = stream.read(min(remaining, PAGE_CHARACTERS))
                if not skipped:
                    raise ValueError(f"{target}: offset {offset} is beyond the end of the file")
                remaining -= len(skipped)
            page = stream.read(PAGE_CHARACTERS + 1)
    except UnicodeDecodeError as error:
        fragment = error.object[max(0, error.start - 30):error.end + 30]
        raise ValueError(f"{target}: invalid UTF-8: {error}; raw={fragment!r}") from error
    has_next = len(page) > PAGE_CHARACTERS
    return {"path": path, "offset": offset, "content": page[:PAGE_CHARACTERS],
            "next_offset": offset + PAGE_CHARACTERS if has_next else None}


def move_skill(source: Path, destination: Path) -> None:
    """Rename an already-validated skill directory without copying or replacing."""
    _directory(source)
    if destination.resolve(strict=False) != destination:
        raise ValueError(f"{destination}: skill destination must not traverse a symbolic link")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(destination):
        raise FileExistsError(f"Skill destination already exists: {destination}")
    source.rename(destination)


def delete_skill(path: Path) -> None:
    """Delete one API-authorized skill directory; retain any real filesystem error."""
    shutil.rmtree(path)
