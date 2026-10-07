"""Shared release protocol for the native and container update controller."""

import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request

PROTOCOL = 1
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?')


def version_key(value: str) -> tuple:
    match = VERSION.fullmatch(value)
    if match is None:
        raise ValueError(f'Unsupported release version: {value!r}')
    return (*map(int, match.group(1, 2, 3)), {'a': 0, 'b': 1, 'rc': 2, None: 3}[match[4]], int(match[5] or 0))


def repository(reference: str) -> str:
    """Image name without tag or digest; a registry port such as 127.0.0.1:5000 stays."""
    name = reference.split('@', 1)[0]
    head, slash, last = name.rpartition('/')
    return head + slash + last.split(':', 1)[0]


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def owned_like_parent(path: Path) -> Path:
    """The Docker updater runs as root; what it writes into the bind-mounted deployment stays editable by its owner."""
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        info = path.parent.stat()
        os.chown(path, info.st_uid, info.st_gid)
    return path


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    owned_like_parent(path)


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={'User-Agent': 'LenBot-Updater', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def release_manifest(value: dict) -> dict:
    if value['manifest_version'] != 1 or value['updater_protocol'] != PROTOCOL:
        raise ValueError('This release requires a different updater protocol; upgrade the controller explicitly first')
    version_key(value['version'])
    if value['tag'] != 'v' + value['version']:
        raise ValueError('Release tag and package version differ')
    for name, item in value['files'].items():
        if Path(name).name != name or re.fullmatch(r'[a-f0-9]{64}', item['sha256']) is None:
            raise ValueError(f'Invalid release file: {name!r}')
    return value


def download(url: str, destination: Path, expected: dict) -> None:
    request = urllib.request.Request(url, headers={'User-Agent': 'LenBot-Updater'})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=60) as response, destination.open('xb') as target:
        while block := response.read(1024 * 1024):
            digest.update(block)
            target.write(block)
    if destination.stat().st_size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
        raise ValueError(f'Download checksum or length differs: {destination.name}')
