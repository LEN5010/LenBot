"""Every update failure leaves the user one concrete next action, and nothing is undone automatically."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/updater'))
import controller as updater
from common import write_json
from native import Native

SHA = 'a' * 64
MANIFEST = {'manifest_version': 1, 'updater_protocol': 1, 'version': '0.2.0', 'tag': 'v0.2.0',
            'bundles': {'linux': 'lenbot-0.2.0-linux.tar.gz'},
            'files': {'lenbot-0.2.0-linux.tar.gz': {'sha256': SHA, 'bytes': 1}}}
RELEASES = [{'tag_name': 'v0.2.0', 'draft': False, 'prerelease': False, 'body': '说明', 'assets': [
    {'name': 'release-manifest.json', 'browser_download_url': 'manifest'},
    {'name': 'lenbot-0.2.0-linux.tar.gz', 'browser_download_url': 'bundle'}]}]


class Backend:
    """Records each step; ``fail`` names the step that raises."""

    def __init__(self, fail: str | None = None, blocked: list[str] | None = None):
        self.fail, self.blocked = fail, blocked or []
        self.current = {'version': '0.1.0'}
        self.running, self.calls, self.discarded = True, [], []

    def step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f'synthetic {name} failure')

    def metadata(self):
        return self.current

    def prepare(self, manifest, archive, work):
        self.step('prepare')
        return {'version': manifest['version']}

    def inspect(self, target):
        self.step('inspect')
        return {'blocked_plugins': self.blocked, 'disabled_plugins': [], 'work_enabled': False}

    def discard(self, target):
        self.discarded.append(target['version'])

    def stop(self):
        self.step('stop')
        self.running = False

    def backup(self, snapshot):
        self.step('backup')

    def migrate(self, target):
        self.step('migrate')

    def select(self, target):
        self.step('select')
        self.current = target

    def start(self):
        self.step('start')
        self.running = True

    def ready(self, target):
        self.step('ready')

    def restore(self, old, snapshot):
        self.step('restore')
        self.current = old


@pytest.fixture
def make(tmp_path, monkeypatch):
    write_json(tmp_path / 'deployment.json', {'mode': 'native', 'platform': 'linux', 'uv': 'uv', 'release_api': 'releases'})
    write_json(tmp_path / 'current.json', {'version': '0.1.0'})
    sources = {'releases': RELEASES, 'manifest': MANIFEST}
    monkeypatch.setattr(updater, 'fetch_json', lambda url: sources[url])
    monkeypatch.setattr(updater, 'download', lambda url, path, expected: path.write_bytes(b'x'))
    made = []

    def build(backend: Backend) -> updater.Controller:
        controller = updater.Controller(tmp_path)
        controller.backend = backend
        made.append(controller)
        return controller
    yield build
    for controller in made:
        controller.log.close()


def run(controller: updater.Controller, action: str, payload: dict | None = None) -> dict:
    controller.submit(action, payload or {})
    deadline = time.monotonic() + 5
    while controller.operation.locked():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return controller.status()


def test_full_update_then_restore_then_start(make):
    backend = Backend()
    controller = make(backend)
    assert controller.status()['actions'] == ['prepare']
    assert run(controller, 'prepare', {'tag': 'v0.2.0'})['actions'] == ['prepare', 'apply']
    assert not list((controller.root / 'updates').glob('candidate-*'))
    done = run(controller, 'apply')
    assert done['status'] == 'complete' and done['current_version'] == '0.2.0' and not done['stopped']
    assert done['actions'] == ['prepare', 'restore']
    restored = run(controller, 'restore')
    assert restored['current_version'] == '0.1.0' and restored['actions'] == ['start']
    assert run(controller, 'start')['status'] == 'complete' and backend.running


def test_download_failure_keeps_host_running_and_removes_candidate(make, monkeypatch):
    def broken(url, path, expected):
        path.write_bytes(b'partial')
        raise ValueError('Download checksum or length differs')
    monkeypatch.setattr(updater, 'download', broken)
    backend = Backend()
    controller = make(backend)
    state = run(controller, 'prepare', {'tag': 'v0.2.0'})
    assert state['status'] == 'failed' and 'checksum' in state['error']
    assert state['actions'] == ['prepare'] and backend.running and 'stop' not in backend.calls
    assert not list((controller.root / 'updates').glob('candidate-*'))


@pytest.mark.parametrize('fail', ['prepare', 'inspect'])
def test_environment_or_compatibility_failure_discards_only_what_was_prepared(make, fail):
    backend = Backend(fail=fail)
    controller = make(backend)
    state = run(controller, 'prepare', {'tag': 'v0.2.0'})
    assert state['actions'] == ['prepare'] and backend.running
    assert backend.discarded == ([] if fail == 'prepare' else ['0.2.0'])


def test_blocked_plugin_is_listed_and_preparing_again_replaces_the_candidate(make):
    backend = Backend(blocked=['fixture: requires host >=0.1,<0.2'])
    controller = make(backend)
    state = run(controller, 'prepare', {'tag': 'v0.2.0'})
    assert 'fixture' in state['error'] and state['actions'] == ['prepare']
    backend.blocked = []
    run(controller, 'prepare', {'tag': 'v0.2.0'})
    backend.discarded.clear()
    assert run(controller, 'prepare', {'tag': 'v0.2.0'})['status'] == 'prepared'
    assert backend.discarded == ['0.2.0']


@pytest.mark.parametrize('fail', ['stop', 'backup'])
def test_failure_before_snapshot_offers_starting_the_unchanged_program(make, fail):
    backend = Backend(fail=fail)
    controller = make(backend)
    run(controller, 'prepare', {'tag': 'v0.2.0'})
    state = run(controller, 'apply')
    assert state['status'] == 'failed' and state['stopped'] and not state['snapshot_complete']
    assert state['actions'] == ['start'] and state['current_version'] == '0.1.0'
    with pytest.raises(ValueError, match='现在不能恢复快照；可以：启动程序'):
        controller.submit('restore', {})
    backend.fail = None
    assert run(controller, 'start')['status'] == 'complete' and backend.running


@pytest.mark.parametrize('fail', ['migrate', 'select', 'start', 'ready'])
def test_failure_after_snapshot_requires_restore_first(make, fail):
    backend = Backend(fail=fail)
    controller = make(backend)
    run(controller, 'prepare', {'tag': 'v0.2.0'})
    state = run(controller, 'apply')
    assert state['status'] == 'failed' and state['snapshot_complete'] and state['actions'] == ['restore']
    with pytest.raises(ValueError, match='现在不能启动程序'):
        controller.submit('start', {})
    restored = run(controller, 'restore')
    assert restored['current_version'] == '0.1.0' and restored['actions'] == ['start']


@pytest.mark.parametrize(('stage', 'stopped', 'snapshot', 'actions'), [
    ('download', False, False, ['prepare']),
    ('backup', True, False, ['start']),
    ('migrate', True, True, ['restore']),
])
def test_controller_restart_mid_operation_keeps_a_way_out(make, stage, stopped, snapshot, actions):
    controller = make(Backend())
    write_json(controller.state_file, {'status': 'applying', 'stage': stage, 'stopped': stopped,
                                       'snapshot_complete': snapshot, 'old': {'version': '0.1.0'}, 'snapshot': 'x'})
    restarted = make(Backend())
    assert restarted.state['status'] == 'failed' and restarted.status()['actions'] == actions


def test_unknown_and_out_of_order_actions_are_refused(make):
    controller = make(Backend())
    with pytest.raises(ValueError, match='未知'):
        controller.submit('rollback', {})
    with pytest.raises(ValueError, match='现在不能停机升级；可以：准备更新'):
        controller.submit('apply', {})


@pytest.mark.skipif(sys.platform == 'win32', reason='process group signal')
def test_native_stop_kills_the_process_group_after_the_timeout(tmp_path):
    write_json(tmp_path / 'deployment.json', {'mode': 'native', 'platform': 'linux', 'uv': 'uv'})
    with (tmp_path / 'log').open('w') as log:
        native = Native(tmp_path, log)
        native.stop_seconds = 0.5
        code = ('import signal, subprocess, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                'child = subprocess.Popen([sys.executable, "-c", "import signal, time; '
                'signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"]); print(child.pid, flush=True); time.sleep(60)')
        native.child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True,
                                        start_new_session=True)
        grandchild = int(native.child.stdout.readline())
        native.stop()
    assert native.child.returncode == -signal.SIGKILL
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail('the host child process outlived the forced stop')
    assert '强制结束' in (tmp_path / 'log').read_text()
