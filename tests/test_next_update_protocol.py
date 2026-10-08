"""Release metadata, plugin compatibility and stopped-data recovery boundaries."""

import json
import errno
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


@pytest.mark.parametrize('corruption', ['missing', 'truncated', 'same-length'])
def test_corrupt_snapshot_never_replaces_current_instance(tmp_path, corruption):
    root, snapshot = tmp_path / 'instance', tmp_path / 'backup'
    root.mkdir()
    (root / 'note.txt').write_bytes(b'original')
    create([str(root)], snapshot)
    (root / 'note.txt').write_bytes(b'current')
    saved = snapshot / '0/note.txt'
    if corruption == 'missing':
        saved.unlink()
    else:
        saved.write_bytes(b'orig' if corruption == 'truncated' else b'corrupt!')
    with pytest.raises(ValueError, match='missing|manifest'):
        restore(root, snapshot)
    assert (root / 'note.txt').read_bytes() == b'current'


@pytest.mark.skipif(sys.platform == 'win32', reason='emulates the Windows rule on POSIX; Windows CI runs it for real in the update smoke')
def test_snapshot_syncs_through_the_writing_handle_and_keeps_read_only_files(tmp_path, monkeypatch):
    """Windows rejects fsync on a read-only handle (EBADF); the backup must not rely on it."""
    import fcntl
    import os
    import stat
    from len_bot.next.maintenance import snapshot as snapshots
    sync = os.fsync

    def windows_fsync(descriptor):
        # Directory syncs only happen on POSIX, where O_RDONLY is the only way to open them.
        regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
        if regular and fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY:
            raise OSError(errno.EBADF, 'Bad file descriptor')
        sync(descriptor)

    monkeypatch.setattr(snapshots.os, 'fsync', windows_fsync)
    root = tmp_path / 'instance'
    pack = root / 'plugins/demo/.git/objects/pack/pack-1.pack'
    pack.parent.mkdir(parents=True)
    pack.write_bytes(b'pack-fixture')
    pack.chmod(0o444)
    (root / 'lenbot.config.json').write_text('{"config_version":3}')
    snapshot = tmp_path / 'backup'
    create([str(root)], snapshot)
    saved = snapshot / '0/plugins/demo/.git/objects/pack/pack-1.pack'
    assert saved.read_bytes() == b'pack-fixture'
    assert stat.S_IMODE(saved.stat().st_mode) == 0o444
    assert saved.stat().st_mtime == pack.stat().st_mtime
    (root / 'lenbot.config.json').write_text('{"config_version":4}')
    restore(root, snapshot)
    assert (root / 'lenbot.config.json').read_text() == '{"config_version":3}'
    assert pack.read_bytes() == b'pack-fixture'


@pytest.mark.parametrize('failure', [errno.EBUSY, errno.EACCES])
def test_restore_preserves_bind_mount_directory_but_reports_other_removal_errors(tmp_path, monkeypatch, failure):
    root = tmp_path / 'instance'
    mounted = root / 'worker' / 'delivery'
    mounted.mkdir(parents=True)
    inode = mounted.stat().st_ino
    (mounted / 'output.txt').write_text('升级前交付')
    (root / 'state.db').write_bytes(b'synthetic database')
    backup = tmp_path / 'backup'
    create([str(root)], backup)
    (mounted / 'output.txt').write_text('升级后交付')
    (mounted / 'extra.txt').write_text('新增')
    rmdir = Path.rmdir

    def kernel_remove(path):
        if path == mounted:
            raise OSError(failure, 'synthetic kernel removal error', str(path))
        rmdir(path)

    monkeypatch.setattr(Path, 'rmdir', kernel_remove)
    if failure == errno.EACCES:
        with pytest.raises(OSError) as error:
            restore(root, backup)
        assert error.value.errno == errno.EACCES
    else:
        restore(root, backup)
        assert mounted.stat().st_ino == inode
        assert (mounted / 'output.txt').read_text() == '升级前交付'
        assert not (mounted / 'extra.txt').exists()
        assert (root / 'state.db').read_bytes() == b'synthetic database'


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
