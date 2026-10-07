"""Release metadata, plugin compatibility and stopped-data recovery boundaries."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from len_bot.next.maintenance.snapshot import create, restore
from len_bot.next.maintenance.upgrade import inspect
from len_bot.next.instance_lock import instance_lock, InstanceBusyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/updater'))
from common import release_manifest, version_key


def test_release_order_and_protocol():
    assert sorted(['0.2.0', '0.2.0rc1', '0.2.0b2', '0.2.0a1', '0.1.9'], key=version_key) == [
        '0.1.9', '0.2.0a1', '0.2.0b2', '0.2.0rc1', '0.2.0']
    with pytest.raises(ValueError, match='Unsupported'):
        version_key('0.2.0-nightly')
    with pytest.raises(ValueError, match='protocol'):
        release_manifest({'manifest_version': 1, 'updater_protocol': 2})
    with pytest.raises(ValueError, match='differ'):
        release_manifest({'manifest_version': 1, 'updater_protocol': 1, 'version': '0.2.0', 'tag': 'v0.3.0'})


def test_snapshot_restores_root_and_external_data_without_snapshotting_lock(tmp_path):
    root, outside = tmp_path / 'instance', tmp_path / 'external'
    root.mkdir()
    outside.mkdir()
    (root / 'lenbot.config.json').write_text('{"config_version":1}')
    (root / 'data.sqlite3-wal').write_bytes(b'wal-fixture')
    (outside / 'memory.md').write_text('升级前正文')
    snapshot = tmp_path / 'backup'
    with instance_lock(root):
        create([str(root), str(outside)], snapshot)
        assert not (snapshot / '0/.lenbot-instance.lock').exists()
        (root / 'lenbot.config.json').write_text('{"config_version":2}')
        (root / 'new.json').write_text('new')
        (outside / 'memory.md').write_text('升级后正文')
        restore(root, snapshot)
        with pytest.raises(InstanceBusyError):
            with instance_lock(root):
                pass
    assert json.loads((root / 'lenbot.config.json').read_text()) == {'config_version': 1}
    assert (root / 'data.sqlite3-wal').read_bytes() == b'wal-fixture'
    assert (outside / 'memory.md').read_text() == '升级前正文'
    assert not (root / 'new.json').exists()


def test_target_version_blocks_enabled_plugin_without_importing_it(tmp_path):
    plugin = tmp_path / 'plugins/fixture'
    plugin.mkdir(parents=True)
    (plugin / '__init__.py').write_text('raise AssertionError("inspection must not import plugin code")')
    (plugin / 'plugin.toml').write_text('''name = "fixture"
version = "1.0.0"
interface = 1
requires_lenbot = ">=0.1,<0.2"
requires_python = ">=3.13"
platforms = ["linux", "darwin", "win32"]
reload = "plugin"
authors = ["fixture"]
license = "GPL-3.0-only"
description = "Compatibility fixture"
''')
    configuration = {'plugins': {'paths': ['plugins'], 'fixture': {}, 'disabled': []}}
    path = tmp_path / 'lenbot.config.json'
    path.write_text(json.dumps(configuration))
    assert inspect(tmp_path, '0.1.1')['blocked_plugins'] == []
    assert 'requires host' in inspect(tmp_path, '0.2.0')['blocked_plugins'][0]
    configuration['plugins']['disabled'] = ['fixture']
    path.write_text(json.dumps(configuration))
    assert inspect(tmp_path, '0.2.0')['blocked_plugins'] == []


def test_restore_removes_a_journal_created_after_the_snapshot_outside_the_instance(tmp_path):
    root, data = tmp_path / 'instance', tmp_path / 'data'
    root.mkdir()
    data.mkdir()
    database = data / 'state.db'
    database.write_bytes(b'old database')
    companions = [str(database.with_name('state.db' + suffix)) for suffix in ('-journal', '-shm', '-wal')]
    snapshot = tmp_path / 'backup'
    create([str(root), str(database), *companions], snapshot)
    database.write_bytes(b'new database')
    database.with_name('state.db-wal').write_bytes(b'frames from the failed new version')
    restore(root, snapshot)
    assert database.read_bytes() == b'old database'
    assert not database.with_name('state.db-wal').exists()


def test_backup_paths_cover_every_external_directory_once(tmp_path):
    """Only role files and plugin sources may live outside the instance; everything else is inside the root copy."""
    from len_bot.next.maintenance.upgrade import data_paths
    from test_next_config import _host_config, _write_config
    root = tmp_path / 'instance'
    source = _host_config()
    source['memory'] = {'backend': 'local', 'local': {'directory': 'memory'}}
    source['plugins'] = {'paths': ['plugins', str(tmp_path / 'shared/plugins'), str(tmp_path / 'shared')],
                         'data_directory': 'plugin-data'}
    _write_config(root, source)
    assert data_paths(root) == [str(root), *sorted(str(path.resolve()) for path in (
        tmp_path / 'private-persona', tmp_path / 'shared'))]


def _instance_with_outside_data(tmp_path):
    root, outside = tmp_path / 'instance', tmp_path / 'external'
    root.mkdir()
    outside.mkdir()
    (root / 'lenbot.config.json').write_text('{"config_version":1}')
    (outside / 'memory.md').write_text('升级前正文')
    return root, outside


def test_snapshot_stops_before_copying_when_the_disk_is_short(tmp_path, monkeypatch):
    from len_bot.next.maintenance import snapshot as snapshots
    root, outside = _instance_with_outside_data(tmp_path)
    monkeypatch.setattr(snapshots.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 1024})())
    with pytest.raises(OSError, match='只剩'):
        create([str(root), str(outside)], tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


def test_restore_that_fails_while_copying_leaves_the_instance_unchanged(tmp_path, monkeypatch):
    from len_bot.next.maintenance import snapshot as snapshots
    root, outside = _instance_with_outside_data(tmp_path)
    create([str(root), str(outside)], tmp_path / 'backup')
    (root / 'lenbot.config.json').write_text('{"config_version":2}')
    (outside / 'memory.md').write_text('升级后正文')
    copy = snapshots.copy_path

    def full_disk(source, target):
        if target.name.startswith('.external'):
            raise OSError(28, 'No space left on device')
        copy(source, target)
    monkeypatch.setattr(snapshots, 'copy_path', full_disk)
    with pytest.raises(OSError, match='No space left'):
        restore(root, tmp_path / 'backup')
    assert json.loads((root / 'lenbot.config.json').read_text()) == {'config_version': 2}
    assert (outside / 'memory.md').read_text() == '升级后正文'
    assert sorted(path.name for path in root.iterdir()) == ['lenbot.config.json']
    assert sorted(path.name for path in tmp_path.iterdir()) == ['backup', 'external', 'instance']

    monkeypatch.setattr(snapshots, 'copy_path', copy)
    restore(root, tmp_path / 'backup')
    assert json.loads((root / 'lenbot.config.json').read_text()) == {'config_version': 1}
    assert (outside / 'memory.md').read_text() == '升级前正文'


def test_inspect_and_paths_run_on_a_read_only_instance(tmp_path):
    """The container updater mounts the instance read-only for these two questions."""
    import os
    import stat
    import subprocess
    root = tmp_path / 'instance'
    root.mkdir()
    (root / 'lenbot.config.json').write_text('{}')
    os.chmod(root, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = subprocess.run([sys.executable, '-m', 'len_bot.next.maintenance.upgrade', 'inspect', '--version', '0.2.0'],
                                cwd=root, capture_output=True, text=True, check=False)
    finally:
        os.chmod(root, stat.S_IRWXU)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['blocked_plugins'] == [] and not (root / 'logs').exists()
