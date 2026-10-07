"""A stable parent process selects versioned Python environments."""

import http.client

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

from common import read_json, repository, write_json

# Task images published with each release; a locally built or other image is left for the owner to update.
OFFICIAL_WORKERS = {'ghcr.io/lendevs/lenbot-worker', 'docker.io/lendevs/lenbot-worker', 'lendevs/lenbot-worker'}

if sys.platform == 'win32':
    import msvcrt
else:
    import fcntl


def instance_busy(root: Path) -> bool:
    """Whether some process holds the host's instance lock (the same lock the host takes at start)."""
    path = root / '.lenbot-instance.lock'
    if not path.exists():
        return False
    with path.open('r+b') as stream:
        try:
            if sys.platform == 'win32':
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(stream, fcntl.LOCK_UN)
        except OSError:
            return True
    return False


def python_path(environment: Path) -> Path:
    return environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


class Native:
    stop_seconds = 180

    def __init__(self, root: Path, log):
        self.root, self.log = root, log
        self.deployment = read_json(root / 'deployment.json')
        self.instance = root / 'instance'
        self.child: subprocess.Popen | None = None
        self.pid_file = root / 'updates/host.pid'

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

    def worker_image(self, target: dict) -> tuple[dict, str | None]:
        """The task settings and the release image they follow, or None when the image is not an official one."""
        worker = read_json(self.instance / 'lenbot.config.json').get('worker')
        if worker is None or repository(worker['image']) not in OFFICIAL_WORKERS:
            return worker, None
        return worker, repository(worker['image']) + ':' + target['version']

    def inspect(self, target: dict) -> dict:
        result = json.loads(self.maintenance('inspect', target=target, capture=True))
        worker, image = self.worker_image(target)
        if image is not None:
            # Pulled before any downtime; the configuration switches only during the stopped migration.
            docker = [worker['docker_binary'], *(['--host', worker['docker_host']] if worker.get('docker_host') else [])]
            self.command([*docker, 'pull', image])
        result['worker_image'] = None if worker is None else image or worker['image']
        result['worker_follows_release'] = image is not None
        return result

    def stop_orphan(self) -> None:
        """A host left running by a controller that was killed outright is stopped before a new one starts."""
        if not self.pid_file.exists():
            return
        pid = int(self.pid_file.read_text())
        if instance_busy(self.instance):
            print(f'上次的宿主进程 {pid} 仍在运行，先停止它', file=self.log)
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], stdout=self.log, stderr=self.log)
            else:
                try:
                    os.killpg(pid, signal.SIGTERM)
                    deadline = time.monotonic() + self.stop_seconds
                    while instance_busy(self.instance) and time.monotonic() < deadline:
                        time.sleep(0.2)
                    if instance_busy(self.instance):
                        os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 10
            while instance_busy(self.instance):
                if time.monotonic() > deadline:
                    raise RuntimeError(f'实例仍被其他进程占用（记录的宿主进程 {pid}）；请先结束它')
                time.sleep(0.2)
        self.pid_file.unlink()

    def start(self) -> None:
        if self.child is not None and self.child.poll() is None:
            return
        self.stop_orphan()
        # UTF-8 mode through the environment so the host the launcher starts gets it too; on Windows a
        # redirected stream would otherwise use the ANSI code page and fail on the first Chinese line.
        self.child = subprocess.Popen([self.python(), '-m', 'len_bot.next.launcher'], cwd=self.instance, stderr=self.log,
            env={**os.environ, 'PYTHONUTF8': '1'},
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            start_new_session=sys.platform != 'win32')
        self.pid_file.parent.mkdir(exist_ok=True)
        self.pid_file.write_text(str(self.child.pid))

    def stop(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == 'win32' else signal.SIGTERM)
            try:
                self.child.wait(timeout=self.stop_seconds)
            except subprocess.TimeoutExpired:
                # Same limit as docker stop -t 180; the host's whole process tree goes, not only its first process.
                print(f'宿主 {self.stop_seconds} 秒内没有退出，强制结束整个进程组', file=self.log)
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.child.pid)], stdout=self.log, stderr=self.log)
                else:
                    os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait()
        else:
            self.stop_orphan()
        self.pid_file.unlink(missing_ok=True)

    def discard(self, target: dict) -> None:
        if target['version'] != self.metadata()['version']:
            shutil.rmtree(self.root / 'releases' / target['version'], ignore_errors=True)

    def backup(self, snapshot: Path) -> None:
        self.maintenance('backup', snapshot=snapshot)

    def migrate(self, target: dict) -> None:
        self.maintenance('apply', target=target)
        worker, image = self.worker_image(target)
        if image is not None:
            config = read_json(self.instance / 'lenbot.config.json')
            config['worker']['image'] = image
            write_json(self.instance / 'lenbot.config.json', config)

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
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                pass
            time.sleep(0.25)
        raise TimeoutError('New host did not become ready within 90 seconds')
