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
    monkeypatch.setattr(prepare, 'NOTES', tmp_path / 'release-notes.md')
    prepare.NOTES.write_text(notes)
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
