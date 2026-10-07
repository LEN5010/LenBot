"""Framework prompts edited from the panel: validation, startup loading and the upgrade rule."""

import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from len_bot.next import prompt_files
from len_bot.next.panel.routes.prompts import register_host_prompts
from len_bot.next.prompt_files import activate, bundled, check, pending, read_prompt, save

TEMPLATE = 'next_expression_principles.md'  # uses $name
PLAIN = 'next_direct.md'  # read as plain text, no placeholders


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(prompt_files, '_running', {})


def test_check_keeps_placeholders_identical_to_the_default():
    check(TEMPLATE, '$name 简短地回复。')
    with pytest.raises(ValueError, match=r'缺少 \$name'):
        check(TEMPLATE, '简短地回复。')
    with pytest.raises(ValueError, match=r'不认识 \$scene'):
        check(TEMPLATE, '$name 在 $scene 里简短地回复。')
    with pytest.raises(ValueError, match=r'\$\$'):
        check(TEMPLATE, '$name 花了 $5。')
    with pytest.raises(ValueError, match='不能为空'):
        check(TEMPLATE, '  \n')
    check(PLAIN, '价格写成 $5 也可以，这个文件不做替换。')


def test_saved_edit_applies_after_activation(tmp_path):
    save(tmp_path, TEMPLATE, '$name 只说一句。')
    assert read_prompt(TEMPLATE) == bundled(TEMPLATE)
    assert pending(tmp_path) is True
    activate(tmp_path)
    assert read_prompt(TEMPLATE) == '$name 只说一句。'
    assert read_prompt(PLAIN) == bundled(PLAIN)
    assert pending(tmp_path) is False


def test_new_version_moves_edits_aside_by_default(tmp_path, monkeypatch):
    save(tmp_path, TEMPLATE, '$name 只说一句。')
    monkeypatch.setattr(prompt_files, 'current_version', lambda: '99.0.0')
    activate(tmp_path)
    folder = tmp_path / 'state' / 'prompts'
    assert read_prompt(TEMPLATE) == bundled(TEMPLATE)
    assert not (folder / TEMPLATE).exists()
    [archive] = (folder / '.replaced').iterdir()
    assert (archive / TEMPLATE).read_text(encoding='utf-8') == '$name 只说一句。'
    assert json.loads((folder / '.state.json').read_text(encoding='utf-8'))['version'] == '99.0.0'


def test_new_version_keeps_edits_when_asked(tmp_path, monkeypatch):
    save(tmp_path, TEMPLATE, '$name 只说一句。')
    folder = tmp_path / 'state' / 'prompts'
    state = json.loads((folder / '.state.json').read_text(encoding='utf-8'))
    prompt_files.save_state(folder, {**state, 'keep_on_update': True})
    monkeypatch.setattr(prompt_files, 'current_version', lambda: '99.0.0')
    activate(tmp_path)
    assert read_prompt(TEMPLATE) == '$name 只说一句。'
    assert not (folder / '.replaced').exists()


def test_panel_routes_edit_reset_and_keep_setting(tmp_path):
    def operator(request: Request) -> str:
        return 'operator'

    app = FastAPI()
    register_host_prompts(app, root=tmp_path, user=operator)
    with TestClient(app) as client:
        overview = client.get('/api/host/prompts').json()
        assert overview['edited_version'] is None and overview['keep_on_update'] is False
        assert {'name': TEMPLATE, 'edited': False} in overview['items']

        assert client.get('/api/host/prompts/next_missing.md').status_code == 404
        refused = client.put(f'/api/host/prompts/{TEMPLATE}', json={'text': '没有占位符'})
        assert refused.status_code == 422 and '$name' in refused.json()['detail']

        saved = client.put(f'/api/host/prompts/{TEMPLATE}', json={'text': '$name 只说一句。'}).json()
        assert {'name': TEMPLATE, 'edited': True} in saved['items'] and saved['edited_version'] == saved['version']
        detail = client.get(f'/api/host/prompts/{TEMPLATE}').json()
        assert detail['text'] == '$name 只说一句。' and detail['edited'] is True
        assert detail['default'] == bundled(TEMPLATE) and detail['placeholders'] == ['name']

        same = client.put(f'/api/host/prompts/{TEMPLATE}', json={'text': bundled(TEMPLATE)}).json()
        assert {'name': TEMPLATE, 'edited': False} in same['items']

        client.put(f'/api/host/prompts/{TEMPLATE}', json={'text': '$name 只说一句。'})
        reset = client.delete(f'/api/host/prompts/{TEMPLATE}').json()
        assert reset['edited_version'] is None

        kept = client.put('/api/host/prompt-settings', json={'keep_on_update': True}).json()
        assert kept['keep_on_update'] is True
        assert client.put('/api/host/prompt-settings', json={'keep_on_update': 'yes'}).status_code == 422
