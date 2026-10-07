"""One instance's explicit update, downtime progress, and snapshot recovery."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlsplit

from common import PROTOCOL, download, fetch_json, owned_like_parent, read_json, release_manifest, repository, version_key, write_json
from native import Native

LABELS = {'prepare': '准备更新', 'apply': '停机升级', 'restore': '恢复快照', 'start': '启动程序'}
LOG_LIMIT = 5 * 1024 * 1024


def rotate(path: Path) -> None:
    """Keep one previous file once a log grows past the limit; checked when the updater starts."""
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        path.replace(path.with_name(path.name + '.1'))


class Controller:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.joinpath('updates').mkdir(exist_ok=True)
        owned_like_parent(self.root / 'updates')
        # updater.jsonl: the updater's own records in the host log's shape; updater.log: raw output of child processes.
        for name in ('updater.jsonl', 'updater.log'):
            rotate(self.root / 'updates' / name)
        self.records = (root / 'updates/updater.jsonl').open('a', encoding='utf-8', buffering=1)
        owned_like_parent(root / 'updates/updater.jsonl')
        self.log = (root / 'updates/updater.log').open('a', encoding='utf-8', buffering=1)
        owned_like_parent(root / 'updates/updater.log')
        self.deployment = read_json(root / 'deployment.json')
        if self.deployment['mode'] == 'native':
            self.backend = Native(root, self.log)
        else:
            from containers import Containers
            self.backend = Containers(root, self.log)
        self.state_file = root / 'updates/state.json'
        self.state = read_json(self.state_file) if self.state_file.exists() else {'status': 'idle', 'stage': 'idle'}
        if self.state['status'] in ('preparing', 'applying', 'restoring'):
            self.state.update(status='failed', error='更新器在操作完成前退出；请查看日志并明确恢复或重新准备。')
            write_json(self.state_file, self.state)
        self.operation = threading.Lock()
        token_file = root / 'updates/control-token'
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), encoding='utf-8')
            token_file.chmod(0o600)
            owned_like_parent(token_file)
        self.token = token_file.read_text(encoding='utf-8')
        self.recovery_token = secrets.token_urlsafe(32)
        self.reference = None

    def record(self, event: str, *, level: str = 'INFO', error: BaseException | str | None = None, **fields) -> None:
        entry = {'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'), 'level': level,
                 'source': 'updater', 'event': event, **fields}
        if isinstance(error, str):
            entry['error'] = {'message': error}
        elif error is not None:
            entry['error'] = {'type': type(error).__name__, 'message': str(error),
                              'traceback': ''.join(traceback.format_exception(error))}
        print(json.dumps(entry, ensure_ascii=False, default=str), file=self.records)

    def stage(self, stage: str, **values) -> None:
        self.state.update(stage=stage, updated_at=time.time(), **values)
        write_json(self.state_file, self.state)
        self.record('stage', level='ERROR' if values.get('status') == 'failed' else 'INFO', stage=stage, **values)
        print(f'[{datetime.now(timezone.utc).isoformat(timespec="seconds")}] {stage}', file=self.log)

    def log_text(self) -> str:
        """The recovery page view: recent updater records, then the tail of child process output."""
        lines = (self.root / 'updates/updater.jsonl').read_text(encoding='utf-8').splitlines()[-200:]
        records = []
        for line in lines:
            entry = json.loads(line)
            extra = {key: value for key, value in entry.items() if key not in ('ts', 'level', 'source', 'event', 'error')}
            text = f"{entry['ts']} {entry['level']} {entry['event']} {json.dumps(extra, ensure_ascii=False)}"
            if 'error' in entry:
                text += '\n' + entry['error'].get('traceback', entry['error']['message'])
            records.append(text)
        output = (self.root / 'updates/updater.log').read_text(encoding='utf-8')[-16000:]
        return '\n'.join(records) + '\n\n--- 进程输出 ---\n' + output

    def actions(self) -> list[str]:
        """What the user can do next; every failure leaves at least one of these."""
        status = self.state['status']
        if status in ('preparing', 'applying', 'restoring'):
            return []
        if status == 'failed' and self.state.get('snapshot_complete'):
            return ['restore']
        if status == 'restored' or (status == 'failed' and self.state.get('stopped')):
            # Before the snapshot completes nothing is migrated or selected, so the stopped program is still the old one.
            return ['start']
        available = ['prepare']
        if status == 'prepared':
            available.append('apply')
        if status == 'complete' and self.state.get('snapshot_complete'):
            available.append('restore')
        return available

    def releases(self) -> list[dict]:
        result = []
        for release in fetch_json(self.deployment['release_api']):
            if release['draft']:
                continue
            tag = release['tag_name']
            if not tag.startswith('v'):
                continue
            version_key(tag[1:])
            assets = {item['name']: item['browser_download_url'] for item in release['assets']}
            result.append({'tag': tag, 'version': tag[1:], 'prerelease': release['prerelease'],
                           'notes': release['body'] or '', 'assets': assets,
                           'installable': 'release-manifest.json' in assets})
        return sorted(result, key=lambda item: version_key(item['version']), reverse=True)

    def submit(self, action: str, payload: dict) -> dict:
        if self.state['status'] in ('preparing', 'applying', 'restoring'):
            raise ValueError('已有一个更新操作正在执行')
        # The finished operation writes its final status a moment before it releases the lock.
        if not self.operation.acquire(timeout=10):
            raise ValueError('已有一个更新操作正在执行')
        try:
            if action not in LABELS:
                raise ValueError('未知更新操作')
            available = self.actions()
            if action not in available:
                raise ValueError(f'现在不能{LABELS[action]}；可以：' + ('、'.join(LABELS[item] for item in available) or '等待当前操作结束'))
            if action == 'prepare':
                tag = payload['tag']
                version_key(tag.removeprefix('v'))
                if not tag.startswith('v'):
                    raise ValueError('更新目标必须是发行标签')
                previous = self.state.get('target') if self.state['status'] == 'prepared' else None
                self.stage('download', status='preparing', error=None, stopped=False, snapshot_complete=False)
                operation = lambda: self.prepare(tag, previous)
            elif action == 'apply':
                self.stage('compatibility', status='applying', error=None)
                operation = self.apply
            elif action == 'restore':
                self.stage('restore', status='restoring', error=None)
                operation = self.restore
            else:
                self.stage('start', status='applying', error=None)
                operation = self.start
        except BaseException:
            self.operation.release()
            raise
        def execute() -> None:
            try:
                operation()
            except Exception as error:
                self.record('operation_failed', level='ERROR', error=error, stage=self.state['stage'])
                self.stage(self.state['stage'], status='failed', error=f'{type(error).__name__}: {error}')
            finally:
                self.operation.release()
        threading.Thread(target=execute, daemon=False).start()
        return {'accepted': True}

    def prepare(self, tag: str, previous: dict | None = None) -> None:
        if previous is not None:
            # Choosing again replaces the earlier candidate instead of leaving its environment or container behind.
            self.backend.discard(previous)
            self.state.pop('target', None)
        release, = [item for item in self.releases() if item['tag'] == tag]
        if version_key(release['version']) <= version_key(self.backend.metadata()['version']):
            raise ValueError('更新只能选择比当前程序更新的版本；回退使用快照恢复')
        raw = fetch_json(release['assets']['release-manifest.json'])
        if raw.get('updater_protocol') != PROTOCOL:
            raise ValueError(self.updater_steps(tag))
        manifest = release_manifest(raw)
        if manifest['tag'] != tag:
            raise ValueError('发行清单不属于所选标签')
        work = self.root / 'updates' / ('candidate-' + secrets.token_hex(8))
        work.mkdir(mode=0o700)
        target = None
        try:
            archive = None
            if self.deployment['mode'] == 'native':
                name = manifest['bundles'][self.deployment['platform']]
                archive = work / name
                download(release['assets'][name], archive, manifest['files'][name])
            self.stage('prepare_environment')
            target = self.backend.prepare(manifest, archive, work)
            self.stage('compatibility')
            check = self.backend.inspect(target)
            if check['blocked_plugins']:
                raise ValueError('请先升级或停用不兼容插件：\n' + '\n'.join(check['blocked_plugins']))
        except BaseException:
            if target is not None:
                self.backend.discard(target)
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.state.update(target=target, manifest=manifest, check=check, notes=release['notes'])
        self.stage('prepared', status='prepared')

    def updater_steps(self, tag: str) -> str:
        """The updater never replaces itself; a newer protocol is switched by one explicit command."""
        if self.deployment['mode'] == 'native':
            return (f'{tag} 需要新版更新器，不能从面板换版。请停止 LenBot，下载 {tag} 的部署包并解压，'
                    f'运行其中的 install.sh upgrade {self.root}（Windows 用 install.ps1 upgrade {self.root}），'
                    '它会同时更新程序和更新器，然后照常启动。')
        version = tag.removeprefix('v')
        image = repository(self.backend.metadata()['host_image']) + '-updater:' + version
        return (f'{tag} 需要新版更新器，请在部署目录 {self.deployment["host_directory"]} 依次运行：\n'
                f'docker run --rm --mount type=bind,source={self.deployment["host_directory"]},target=/deployment '
                f'--entrypoint python {image} /opt/lenbot-updater/init.py --version {version} --updater-only\n'
                f'docker compose -p {self.deployment["project"]} -f host.updater.compose.yaml up -d\n'
                '新的更新器启动后，回到这里再准备更新。')

    def apply(self) -> None:
        target = self.state['target']
        check = self.backend.inspect(target)
        if check['blocked_plugins']:
            raise ValueError('配置或插件已改变，请先处理：\n' + '\n'.join(check['blocked_plugins']))
        old = self.backend.metadata()
        self.state['old'] = old
        self.stage('stop', stopped=True)
        self.backend.stop()
        snapshot = self.root / 'backups' / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4))
        self.stage('backup', snapshot=str(snapshot))
        self.backend.backup(snapshot)
        self.stage('migrate', snapshot_complete=True)
        self.backend.migrate(target)
        self.stage('select')
        self.backend.select(target)
        self.publish_reference()
        self.stage('start')
        try:
            self.backend.start()
            self.backend.ready(target)
        except Exception:
            self.backend.stop()
            raise
        self.stage('complete', status='complete', stopped=False)

    def restore(self) -> None:
        self.backend.restore(self.state['old'], Path(self.state['snapshot']))
        self.publish_reference()
        self.stage('restored', status='restored', stopped=True)

    def start(self) -> None:
        self.backend.start()
        self.backend.ready(self.backend.metadata())
        self.stage('complete', status='complete', stopped=False)

    def publish_reference(self) -> None:
        if self.reference is not None:
            self.backend.publish_reference(self.reference) if self.deployment['mode'] == 'docker' else write_json(
                self.backend.instance / '.runtime/update-control.json', self.reference)

    def status(self) -> dict:
        return {**self.state, 'current_version': self.backend.metadata()['version'], 'mode': self.deployment['mode'],
                'actions': self.actions()}


def server(controller: Controller) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Request lines are only kept for failures; the recovery page polls state continuously.
            if args and str(args[1] if len(args) > 1 else '')[:1] in {'4', '5'}:
                controller.record('http', level='WARNING', request=format % args)

        def send(self, code: int, value) -> None:
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            supplied = self.headers.get('Authorization', '').removeprefix('Bearer ')
            return secrets.compare_digest(supplied, controller.token) or secrets.compare_digest(supplied, controller.recovery_token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                data = Path(__file__).with_name('recovery.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
                return
            if not self.authorized():
                self.send(401, {'detail': '使用管理面板交接的更新链接'})
                return
            try:
                if path == '/api/status':
                    self.send(200, controller.status())
                elif path == '/api/releases':
                    self.send(200, controller.releases())
                elif path == '/api/session':
                    self.send(200, {'url': controller.reference['public_url'] + '/#session=' + controller.recovery_token})
                elif path == '/api/log':
                    self.send(200, {'text': controller.log_text()})
                else:
                    self.send(404, {'detail': 'Unknown endpoint'})
            except Exception as error:
                self.send(422, {'detail': f'{type(error).__name__}: {error}'})

        def do_POST(self):
            if not self.authorized():
                self.send(401, {'detail': '使用管理面板交接的更新链接'})
                return
            path = urlsplit(self.path).path
            if path == '/api/shutdown':
                # Same as a stop signal; used where a service manager cannot send one (Windows scheduled task).
                self.send(202, {'accepted': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if not path.startswith('/api/'):
                self.send(404, {'detail': 'Unknown endpoint'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length > 4096:
                    raise ValueError('更新请求过大')
                payload = json.loads(self.rfile.read(length) or b'{}')
                self.send(202, controller.submit(path.removeprefix('/api/'), payload))
            except (ValueError, KeyError) as error:
                self.send(409, {'detail': str(error)})
    address = controller.deployment.get('listen', {'host': '127.0.0.1', 'port': 0})
    http = ThreadingHTTPServer((address['host'], address['port']), Handler)
    endpoint = f'http://127.0.0.1:{http.server_port}'
    controller.reference = {'protocol': 1, 'endpoint': controller.deployment.get('endpoint', endpoint),
                            'public_url': controller.deployment.get('public_url', endpoint), 'token': controller.token}
    controller.publish_reference()
    return http


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    controller = Controller(args.root)
    http = server(controller)
    print(f'更新与恢复：{controller.reference["public_url"]}/#session={controller.recovery_token}', flush=True)
    def stop(signum, frame):
        threading.Thread(target=http.shutdown, daemon=True).start()
    for sig in ((signal.SIGINT, signal.SIGTERM, signal.SIGBREAK) if sys.platform == 'win32' else (signal.SIGINT, signal.SIGTERM)):
        signal.signal(sig, stop)
    # A stop for the update stays in effect across controller restarts until the user starts or restores.
    if controller.deployment['mode'] == 'native' and not controller.state.get('stopped'):
        controller.backend.start()
    try:
        http.serve_forever()
    finally:
        if controller.operation.locked():
            # A running update must complete its current step before a service stop returns.
            with controller.operation:
                pass
        if controller.deployment['mode'] == 'native':
            controller.backend.stop()
        http.server_close()
        controller.log.close()
        controller.records.close()


if __name__ == '__main__':
    main()
