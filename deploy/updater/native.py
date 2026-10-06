"""A stable parent process selects versioned Python environments."""

import json
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

from common import read_json, write_json


def python_path(environment: Path) -> Path:
    return environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


class Native:
    def __init__(self, root: Path, log):
        self.root, self.log = root, log
        self.deployment = read_json(root / 'deployment.json')
        self.instance = root / 'instance'
        self.child: subprocess.Popen | None = None

    def metadata(self) -> dict:
        return read_json(self.root / 'current.json')

    def python(self, metadata: dict | None = None) -> str:
        value = self.metadata() if metadata is None else metadata
        return str(python_path(self.root / 'releases' / value['version'] / '.venv'))

    def command(self, arguments: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
        result = subprocess.run(arguments, cwd=cwd, stdout=subprocess.PIPE if capture else self.log,
                                stderr=self.log, text=True, check=True)
        return result.stdout if capture else ''

    def maintenance(self, action: str, *, target: dict | None = None, snapshot: Path | None = None, capture: bool = False) -> str:
        arguments = [self.python(target), '-X', 'utf8', '-m', 'len_bot.next.maintenance.upgrade', action]
        if action == 'inspect':
            arguments += ['--version', target['version']]
        if snapshot is not None:
            arguments += ['--snapshot', str(snapshot)]
        return self.command(arguments, cwd=self.instance, capture=capture)

    def prepare(self, manifest: dict, archive: Path, work: Path) -> dict:
        unpacked = work / 'package'
        unpacked.mkdir()
        if archive.suffix == '.zip':
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if not (unpacked / name).resolve().is_relative_to(unpacked.resolve()):
                        raise ValueError(f'Archive path escapes package: {name}')
                bundle.extractall(unpacked)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(unpacked, filter='data')
        package, = unpacked.iterdir()
        metadata = read_json(package / 'release.json')
        if metadata['version'] != manifest['version'] or metadata['platform'] != self.deployment['platform']:
            raise ValueError('Downloaded package version or platform differs from the selected release')
        self.command([self.deployment['uv'], 'run', '--no-project', '--python', '3.13',
                      str(package / 'install.py'), 'prepare', str(self.root)])
        return metadata

    def inspect(self, target: dict) -> dict:
        return json.loads(self.maintenance('inspect', target=target, capture=True))

    def start(self) -> None:
        if self.child is not None and self.child.poll() is None:
            raise RuntimeError('The host is already running')
        self.child = subprocess.Popen([self.python(), '-X', 'utf8', '-m', 'len_bot.next.launcher'], cwd=self.instance, stderr=self.log,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            start_new_session=sys.platform != 'win32')

    def stop(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == 'win32' else signal.SIGTERM)
            self.child.wait(timeout=180)

    def backup(self, snapshot: Path) -> None:
        self.maintenance('backup', snapshot=snapshot)

    def migrate(self, target: dict) -> None:
        self.maintenance('apply', target=target)

    def select(self, target: dict) -> None:
        write_json(self.root / 'current.json', target)

    def restore(self, old: dict, snapshot: Path) -> None:
        self.stop()
        self.maintenance('restore', target=old, snapshot=snapshot)
        self.select(old)

    def ready(self, target: dict) -> None:
        config = read_json(self.instance / 'lenbot.config.json')
        host = config['panel']['host']
        if host in ('0.0.0.0', '::'):
            host = '127.0.0.1'
        url = f'http://{host}:{config["panel"]["port"]}/api/host/ready'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise RuntimeError(f'New host exited with {self.child.returncode}')
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    status = json.load(response)
                if status['version'] != target['version']:
                    raise ValueError('The panel is serving a different release version')
                if status['ready']:
                    return
                if status['status'] == 'failed':
                    raise RuntimeError('New runtime initialization failed; see the host log')
            except urllib.error.URLError:
                pass
            time.sleep(0.25)
        raise TimeoutError('New host did not become ready within 90 seconds')
