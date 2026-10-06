"""One instance's explicit update, downtime progress, and snapshot recovery."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit

from common import download, fetch_json, read_json, release_manifest, version_key, write_json
from native import Native


class Controller:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.joinpath('updates').mkdir(exist_ok=True)
        self.log = (root / 'updates/updater.log').open('a', encoding='utf-8', buffering=1)
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
        self.token = token_file.read_text(encoding='utf-8')
        self.recovery_token = secrets.token_urlsafe(32)
        self.reference = None

    def stage(self, stage: str, **values) -> None:
        self.state.update(stage=stage, updated_at=time.time(), **values)
        write_json(self.state_file, self.state)
        print(stage, file=self.log)

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
        if not self.operation.acquire(blocking=False):
            raise ValueError('已有一个更新操作正在执行')
        try:
            if action == 'prepare':
                if self.state.get('snapshot_complete') and self.state['status'] == 'failed':
                    raise ValueError('上次停机升级失败，请先恢复；不能覆盖恢复记录')
                tag = payload['tag']
                version_key(tag.removeprefix('v'))
                if not tag.startswith('v'):
                    raise ValueError('更新目标必须是发行标签')
                self.stage('download', status='preparing', error=None, stopped=False, snapshot_complete=False)
                operation = lambda: self.prepare(tag)
            elif action == 'apply':
                if self.state['status'] != 'prepared':
                    raise ValueError('先准备一个兼容的更新版本')
                self.stage('stop', status='applying', error=None)
                operation = self.apply
            elif action == 'restore':
                if not self.state.get('snapshot_complete'):
                    raise ValueError('没有已完成的升级前快照')
                self.stage('restore', status='restoring', error=None)
                operation = self.restore
            elif action == 'start':
                if self.state['status'] not in ('restored', 'idle', 'complete'):
                    raise ValueError('先恢复已知可用的版本，再启动')
                self.stage('start', status='applying')
                operation = self.start
            else:
                raise ValueError('未知更新操作')
        except BaseException:
            self.operation.release()
            raise
        def execute() -> None:
            try:
                operation()
            except Exception as error:
                traceback.print_exc(file=self.log)
                self.stage(self.state['stage'], status='failed', error=f'{type(error).__name__}: {error}')
            finally:
                self.operation.release()
        threading.Thread(target=execute, daemon=False).start()
        return {'accepted': True}

    def prepare(self, tag: str) -> None:
        release, = [item for item in self.releases() if item['tag'] == tag]
        if version_key(release['version']) <= version_key(self.backend.metadata()['version']):
            raise ValueError('更新只能选择比当前程序更新的版本；回退使用快照恢复')
        manifest = release_manifest(fetch_json(release['assets']['release-manifest.json']))
        if manifest['tag'] != tag:
            raise ValueError('发行清单不属于所选标签')
        work = self.root / 'updates' / ('candidate-' + secrets.token_hex(8))
        work.mkdir(mode=0o700)
        archive = None
        if self.deployment['mode'] == 'native':
            name = manifest['bundles'][self.deployment['platform']]
            archive = work / name
            download(release['assets'][name], archive, manifest['files'][name])
        self.stage('prepare_environment')
        target = self.backend.prepare(manifest, archive, work)
        self.stage('compatibility')
        check = self.backend.inspect(target)
        self.state.update(target=target, manifest=manifest, check=check, notes=release['notes'], work=str(work))
        if check['blocked_plugins']:
            raise ValueError('请先升级或停用不兼容插件：\n' + '\n'.join(check['blocked_plugins']))
        self.stage('prepared', status='prepared')

    def apply(self) -> None:
        target = self.state['target']
        check = self.backend.inspect(target)
        if check['blocked_plugins']:
            raise ValueError('配置或插件已改变，请先处理：\n' + '\n'.join(check['blocked_plugins']))
        old = self.backend.metadata()
        self.state['old'] = old
        self.backend.stop()
        snapshot = self.root / 'backups' / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + secrets.token_hex(4))
        self.stage('backup', stopped=True, snapshot=str(snapshot))
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
        return {**self.state, 'current_version': self.backend.metadata()['version'], 'mode': self.deployment['mode']}


def server(controller: Controller) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            print(format % args, file=controller.log)

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
                    self.send(200, {'text': (controller.root / 'updates/updater.log').read_text(encoding='utf-8')[-16000:]})
                else:
                    self.send(404, {'detail': 'Unknown endpoint'})
            except Exception as error:
                self.send(422, {'detail': f'{type(error).__name__}: {error}'})

        def do_POST(self):
            if not self.authorized():
                self.send(401, {'detail': '使用管理面板交接的更新链接'})
                return
            path = urlsplit(self.path).path
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
    if controller.deployment['mode'] == 'native' and controller.state['status'] in ('idle', 'complete', 'prepared'):
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


if __name__ == '__main__':
    main()
