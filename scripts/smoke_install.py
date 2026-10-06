"""Install a built release the way a user would and walk first setup to a logged-in panel, then stop it.

``package`` installs the deployment package for this platform and answers the browser setup wizard.
``docker`` uses an already built host image with the packaged Compose recipe and offline initialization,
on new uniquely named volumes that are removed afterwards. Both use only the standard library and need
no OneBot service or model: delivery is simulated and nothing is sent.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tarfile
import zipfile
import threading
import time
import urllib.error
import urllib.request

PLATFORM = {'linux': 'linux', 'darwin': 'macos', 'win32': 'windows'}
STEP_SECONDS = 90
STOP_SECONDS = 30


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class Output:
    """Collect the process output in the background so a full pipe never blocks it."""

    def __init__(self, stream) -> None:
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        for line in iter(stream.readline, ''):
            self.lines.append(line)

    def find(self, pattern: re.Pattern, deadline: float) -> re.Match:
        while time.monotonic() < deadline:
            for line in self.lines:
                if match := pattern.search(line):
                    return match
            time.sleep(0.2)
        raise TimeoutError(f'process output never matched {pattern.pattern!r}')

    def tail(self, count: int = 80) -> str:
        return ''.join(self.lines[-count:])


def request(opener, url: str, *, body: dict | None = None, headers: dict | None = None) -> tuple[int, str]:
    data = None if body is None else json.dumps(body).encode()
    item = urllib.request.Request(url, data=data, method='GET' if body is None else 'POST',
                                  headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with opener.open(item, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def wait_until_served(opener, url: str, deadline: float, alive) -> str:
    while time.monotonic() < deadline:
        if not alive():
            raise RuntimeError(f'LenBot stopped before {url} answered')
        try:
            status, text = request(opener, url)
            if status == 200:
                return text
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f'{url} did not answer 200')


def check_panel(port: int, username: str, password: str, alive) -> None:
    panel = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    wait_until_served(opener, panel + '/api/host/ready', time.monotonic() + STEP_SECONDS, alive)
    page = wait_until_served(opener, panel + '/', time.monotonic() + STEP_SECONDS, alive)
    assert 'id="app"' in page, page[:200]
    status, text = request(opener, panel + '/api/auth/login', body={'username': username, 'password': password})
    assert status == 200, f'login answered {status}: {text}'
    status, text = request(opener, panel + '/api/host/state')
    assert status == 200, f'host state answered {status}: {text[:500]}'
    scenes = [item['scene'] for item in json.loads(text)['scenes']]
    assert scenes == ['onebot:group:80001'], scenes


def unpack(artifacts: Path, work: Path, platform: str | None = None) -> Path:
    platform = platform or PLATFORM.get(sys.platform)
    if platform is None:
        raise ValueError(f'No deployment package for platform {sys.platform}')
    extension = '.zip' if platform == 'windows' else '.tar.gz'
    bundles = sorted(artifacts.glob(f'lenbot-*-{platform}{extension}'))
    if len(bundles) != 1:
        raise ValueError(f'Expected one {platform} package in {artifacts}, found {[path.name for path in bundles]}')
    if platform == 'windows':
        with zipfile.ZipFile(bundles[0]) as archive:
            archive.extractall(work / 'package')
    else:
        with tarfile.open(bundles[0]) as archive:
            archive.extractall(work / 'package', filter='data')
    folder, = (work / 'package').iterdir()
    return folder


def smoke_package(artifacts: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=False)
    bundle = unpack(artifacts, work)
    root = work / 'lenbot'
    install = ['uv', 'run', '--no-project', '--python', '3.13', str(bundle / 'install.py')] if sys.platform == 'win32' else [str(bundle / 'install.sh')]
    subprocess.run([*install, 'install', str(root)], check=True)

    panel_port = free_port()
    launch = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'run.ps1')] if sys.platform == 'win32' else [str(root / 'run')]
    process = subprocess.Popen(launch, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=sys.platform != 'win32',
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0)
    output = Output(process.stdout)
    result = {'package': bundle.name, 'root': str(root)}
    try:
        match = output.find(re.compile(r'http://127\.0\.0\.1:(\d+)/#token=(\S+)'), time.monotonic() + STEP_SECONDS)
        setup = f'http://127.0.0.1:{match[1]}'
        opener = urllib.request.build_opener()
        page = wait_until_served(opener, setup + '/', time.monotonic() + STEP_SECONDS, lambda: process.poll() is None)
        assert '<html' in page.lower(), page[:200]
        status, text = request(opener, setup + '/api/setup', body={
            'bot_id': 'onebot:90001', 'owners': ['onebot:70001'], 'timezone': 'Asia/Shanghai', 'delivery': 'simulated',
            'onebot': {'mode': 'forward_ws', 'ws_url': f'ws://127.0.0.1:{free_port()}', 'access_token': 'smoke-token'},
            'provider': {'api': 'openai-chat', 'base_url': f'http://127.0.0.1:{free_port()}/v1', 'api_key': 'smoke'},
            'mind': {'provider': 'primary', 'model': 'smoke', 'context_window_tokens': 8192},
            'compaction': {'input_tokens': 6000},
            'scene': 'onebot:group:80001', 'persona_id': 'smoke', 'persona_name': '冒烟角色', 'brief': '安装检查',
            'voice_text': '简短', 'boundaries': '只用于安装检查', 'panel_port': panel_port,
            'username': 'smoke', 'password': 'smoke-password'}, headers={'X-Setup-Token': match[2]})
        assert status == 200, f'first setup answered {status}: {text}'
        result['setup'] = 'saved'

        check_panel(panel_port, 'smoke', 'smoke-password', lambda: process.poll() is None)
        result['panel'] = 'logged in'
    except BaseException:
        print(output.tail(), file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == 'win32' else signal.SIGTERM)
            try:
                process.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                print(output.tail(), file=sys.stderr)
                raise RuntimeError(f'run did not stop within {STOP_SECONDS}s of SIGTERM')
        output.thread.join(5)
    result['exit'] = process.returncode
    if process.returncode != 0:
        print(output.tail(), file=sys.stderr)
        raise RuntimeError(f'run exited with {process.returncode} after SIGTERM')
    return result


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f'Packaged recipe no longer contains exactly one {old!r}')
    return text.replace(old, new)


def smoke_docker(artifacts: Path, work: Path, image: str) -> dict:
    work.mkdir(parents=True, exist_ok=False)
    bundle = unpack(artifacts, work, 'linux')
    project = f'lenbot-smoke-{time.time_ns()}'
    metadata = json.loads((bundle / 'release.json').read_text())
    volumes = {'lenbot-data': project + '-data', 'lenbot-python-' + metadata['version']: project + '-python'}
    port = free_port()
    recipe = (bundle / 'deploy/current/host.compose.yaml').read_text()
    recipe = replace_once(recipe, f'image: ghcr.io/lendevs/lenbot:{metadata["version"]}', f'image: {image}')
    recipe = replace_once(recipe, '127.0.0.1:11307:11307', f'127.0.0.1:{port}:11307')
    for old, new in volumes.items():
        recipe = replace_once(recipe, f'name: {old}', f'name: {new}')
    compose_file = work / 'host.compose.yaml'
    compose_file.write_text(recipe)
    setup = json.loads((bundle / 'deploy/current/first-setup.example.json').read_text())
    setup['password'] = 'smoke-password'
    setup['provider']['api_key'] = 'smoke'
    compose = ['docker', 'compose', '-f', str(compose_file), '-p', project]
    result = {'package': bundle.name, 'image': image}

    def alive() -> bool:
        state = subprocess.run([*compose, 'ps', '--status', 'running', '-q'], capture_output=True, text=True)
        return bool(state.stdout.strip())

    def logs() -> None:
        subprocess.run([*compose, 'logs', '--tail', '80'])

    for volume in volumes.values():
        subprocess.run(['docker', 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL)
    try:
        subprocess.run([*compose, 'run', '--rm', '--no-deps', '-T', '--entrypoint', '/opt/lenbot/.venv/bin/python',
                        'lenbot', '-m', 'len_bot.next.maintenance.initialize'],
                       input=json.dumps(setup, ensure_ascii=False), text=True, check=True)
        result['setup'] = 'initialized'
        subprocess.run([*compose, 'up', '-d'], check=True)
        try:
            check_panel(port, setup['username'], 'smoke-password', alive)
        except BaseException:
            logs()
            raise
        result['panel'] = 'logged in'
        subprocess.run([*compose, 'stop', '--timeout', str(STOP_SECONDS)], check=True)
        container = subprocess.run([*compose, 'ps', '-a', '-q'], capture_output=True, text=True, check=True).stdout.split()
        code = subprocess.run(['docker', 'inspect', '-f', '{{.State.ExitCode}}', *container],
                              capture_output=True, text=True, check=True).stdout.split()
        result['exit'] = [int(item) for item in code]
        if result['exit'] != [0]:
            logs()
            raise RuntimeError(f'container exited with {result["exit"]} after stop')
    finally:
        subprocess.run([*compose, 'down'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'rm', *volumes.values()], stdout=subprocess.DEVNULL)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('package', 'docker'))
    parser.add_argument('artifacts', type=Path, help='artifacts/ from build_release.py, containing the deployment packages')
    parser.add_argument('work', type=Path, help='New directory for the unpacked package and the installation')
    parser.add_argument('--image', help='docker: host image built from the same artifacts')
    args = parser.parse_args()
    if args.mode == 'docker' and not args.image:
        parser.error('docker needs --image')
    artifacts, work = args.artifacts.resolve(), args.work.resolve()
    result = smoke_package(artifacts, work) if args.mode == 'package' else smoke_docker(artifacts, work, args.image)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
