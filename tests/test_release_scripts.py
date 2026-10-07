"""Release preparation edits only the version and refuses draft notes; the smoke recipe edits fail loudly."""

import importlib.util
from pathlib import Path
import tomllib

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_is_set_only_in_project_table(tmp_path, monkeypatch):
    prepare = load('prepare_release')
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "len-bot"\nversion = "0.1.0"\n\n[tool.other]\nversion = "9.9.9"\n')
    monkeypatch.setattr(prepare, 'ROOT', tmp_path)
    assert prepare.set_version('0.2.0rc1') == '0.1.0'
    data = tomllib.loads((tmp_path / 'pyproject.toml').read_text())
    assert data['project']['version'] == '0.2.0rc1' and data['tool']['other']['version'] == '9.9.9'


@pytest.mark.parametrize(('notes', 'problems'), [
    ('# LenBot 0.2.0\n\n正文\n', 0),
    ('# 版本说明（草稿）\n\n正文\n', 1),
    ('# LenBot 0.2.0\n\n（发布时填写：各平台结果。）\n', 1),
    ('# LenBot 0.1.0\n\n（发布时填写：各平台结果。）\n', 2),
])
def test_draft_notes_are_reported(tmp_path, monkeypatch, notes, problems):
    prepare = load('prepare_release')
    monkeypatch.setattr(prepare, 'NOTES', tmp_path)
    (tmp_path / 'v0.2.0.md').write_text(notes)
    assert len(prepare.notes_problems('0.2.0')) == problems


def test_current_notes_are_still_a_draft():
    prepare = load('prepare_release')
    assert prepare.notes_problems(tomllib.loads((SCRIPTS.parent / 'pyproject.toml').read_text())['project']['version'])


def test_packaged_recipe_still_has_the_values_the_docker_smoke_replaces():
    smoke = load('smoke_install')
    recipe = (SCRIPTS.parent / 'deploy/current/host.compose.yaml').read_text()
    for old in ('image: lenbot-current:local', '127.0.0.1:11307:11307', 'name: lenbot-data', 'name: lenbot-python-r1'):
        recipe = smoke.replace_once(recipe, old, 'replaced')
    with pytest.raises(ValueError):
        smoke.replace_once(recipe, 'name: lenbot-data', 'again')


def test_local_candidate_from_changed_sources_is_marked_dirty(tmp_path):
    import subprocess
    metadata = load('release_metadata')
    git = lambda *args: subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    git('init', '-q')
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/module.py').write_text('a = 1\n')
    git('add', '.')
    git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-qm', 'base')
    head = git('rev-parse', 'HEAD').stdout.strip()
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'artifacts/images.json').write_text('{}')
    assert metadata.source_revision(tmp_path) == head
    (tmp_path / 'src/new.py').write_text('b = 2\n')
    assert metadata.source_revision(tmp_path) == head + '-dirty'


def test_missing_version_notes_are_reported(tmp_path, monkeypatch):
    prepare = load('prepare_release')
    monkeypatch.setattr(prepare, 'NOTES', tmp_path)
    assert prepare.notes_problems('0.3.0') == ['缺少 ' + str(Path(tmp_path.name) / 'v0.3.0.md')]


def test_manifest_reads_the_actual_interface_and_formats(tmp_path):
    import json
    metadata = load('release_metadata')
    project = SCRIPTS.parent
    (tmp_path / 'lenbot-0.2.0-linux.tar.gz').write_bytes(b'synthetic')
    manifest = json.loads(metadata.write_manifest(project, tmp_path, revision='synthetic').read_text())
    from len_bot.plugin import INTERFACE
    from len_bot.next.config import CONFIG_VERSION
    from len_bot.next.storage.store import FORMAT_VERSION
    assert manifest['plugin_interface'] == INTERFACE
    assert manifest['formats']['config'] == CONFIG_VERSION and manifest['formats']['business'] == FORMAT_VERSION
    assert manifest['files']['lenbot-0.2.0-linux.tar.gz']['bytes'] == len(b'synthetic')


def test_plugin_package_takes_tracked_runtime_files_and_this_versions_notes(tmp_path):
    import subprocess
    from zipfile import ZipFile
    packaging = load('package_plugin')
    git = lambda *args: subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True, text=True)
    git('init', '-q')
    (tmp_path / 'plugin.toml').write_text('name = "sample"\nversion = "1.2.0"\n[model]\ninstructions = "prompts/tools.md"\n')
    (tmp_path / '__init__.py').write_text('')
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/tools.md').write_text('说明')
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_x.py').write_text('')
    (tmp_path / 'catalog-entry.json').write_text('{}')
    (tmp_path / 'CHANGELOG.md').write_text('# 1.2.0\n\n本版说明。\n\n## 细节\n\n子节。\n\n# 1.1.0\n\n旧版。\n')
    (tmp_path / 'untracked.py').write_text('')
    git('add', 'plugin.toml', '__init__.py', 'prompts', 'tests', 'catalog-entry.json', 'CHANGELOG.md')
    output = packaging.package(tmp_path, tmp_path / 'out' / 'sample.zip')
    with ZipFile(output) as archive:
        assert sorted(archive.namelist()) == ['CHANGELOG.md', '__init__.py', 'plugin.toml', 'prompts/tools.md']
    assert packaging.release_notes(tmp_path, '1.2.0') == '本版说明。\n\n## 细节\n\n子节。\n'
    with pytest.raises(ValueError, match='no "# 9.9.9" section'):
        packaging.release_notes(tmp_path, '9.9.9')


def test_catalog_sync_pins_pushed_commits_and_replaces_entries_by_name(tmp_path):
    import json
    import subprocess
    sync = load('sync_plugin_catalog')
    remote, plugin = tmp_path / 'remote.git', tmp_path / 'plugin'
    run = lambda *args, cwd=plugin: subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
    run('init', '-q', '--bare', str(remote), cwd=tmp_path)
    plugin.mkdir()
    run('init', '-q', '-b', 'main')
    run('remote', 'add', 'origin', str(remote))
    entry = {'name': 'sample', 'version': '1.0.0', 'interface': 1, 'ref': 'main'}
    (plugin / 'catalog-entry.json').write_text(json.dumps(entry))
    (plugin / 'plugin.toml').write_text('name = "sample"\nversion = "1.0.0"\ninterface = 1\n')
    run('add', '.')
    run('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', 'one')
    with pytest.raises(ValueError, match='还不在任何远端分支上'):
        sync.pinned_entry(plugin)
    run('push', '-q', 'origin', 'main')
    commit = run('rev-parse', 'HEAD').stdout.strip()
    assert sync.pinned_entry(plugin) == {**entry, 'ref': commit}

    (plugin / 'plugin.toml').write_text('name = "sample"\nversion = "1.1.0"\ninterface = 1\n')
    run('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-am', 'two')
    with pytest.raises(ValueError, match="version 是 '1.0.0'，plugin.toml 是 '1.1.0'"):
        sync.pinned_entry(plugin)

    catalog = {'version': 1, 'entries': [{'name': 'other'}, {'name': 'sample', 'ref': 'old'}]}
    merged = sync.merge(catalog, [{'name': 'sample', 'ref': 'new'}, {'name': 'added'}])
    assert merged['entries'] == [{'name': 'other'}, {'name': 'sample', 'ref': 'new'}, {'name': 'added'}]
