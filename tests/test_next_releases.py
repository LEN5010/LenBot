"""Published release list parsing and the newer-version answer the panel shows."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from packaging.version import Version

from len_bot.next.panel.routes.updates import register_host_updates
from len_bot.next.runtime.releases import ReleaseCheck, compare, current_version, github_releases


def _release(tag: str, *, prerelease: bool = False, draft: bool = False, body: str | None = '说明') -> dict:
    # Shape of https://api.github.com/repos/<owner>/<repo>/releases, trimmed to the fields that are read.
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft, 'body': body,
            'html_url': f'https://github.com/lendevs/LenBot/releases/tag/{tag}', 'assets': []}


def test_github_list_keeps_published_version_tags_newest_first():
    raw = [_release('v0.2.0'), _release('v0.3.0rc1', prerelease=True), _release('v0.2.1', body=None),
           _release('v0.4.0', draft=True), _release('nightly'), _release('vnext')]
    assert github_releases(raw) == [
        {'tag': 'v0.3.0rc1', 'version': '0.3.0rc1', 'prerelease': True, 'notes': '说明'},
        {'tag': 'v0.2.1', 'version': '0.2.1', 'prerelease': False, 'notes': ''},
        {'tag': 'v0.2.0', 'version': '0.2.0', 'prerelease': False, 'notes': '说明'},
    ]


def test_github_error_body_is_refused_with_its_text():
    with pytest.raises(ValueError, match='API rate limit exceeded'):
        github_releases({'message': 'API rate limit exceeded for 203.0.113.1.'})


@pytest.mark.parametrize(('current', 'latest', 'update', 'preview'), [
    ('0.2.0', '0.2.1', True, '0.3.0rc1'),
    ('0.2.1', '0.2.1', False, '0.3.0rc1'),
    ('0.3.0rc1', '0.2.1', False, None),
    ('0.4.0', '0.2.1', False, None),
])
def test_compare_names_newest_stable_and_a_newer_prerelease(current, latest, update, preview):
    releases = github_releases([_release('v0.2.0'), _release('v0.2.1'), _release('v0.3.0rc1', prerelease=True)])
    result = compare(releases, current)
    assert (result['latest'], result['update_available'], result['latest_prerelease']) == (latest, update, preview)
    assert [release['newer'] for release in result['releases']] == [
        Version(release['version']) > Version(current) for release in result['releases']]


@pytest.mark.asyncio
async def test_check_keeps_the_last_answer_and_the_last_error():
    answers = [github_releases([_release('v9.0.0')]), ValueError('GitHub releases response is not a list: {}')]

    async def load():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    check = ReleaseCheck(load, enabled=False, current='0.2.0')
    assert check.state()['update_available'] is None and check.state()['checked_at'] is None
    assert [release['newer'] for release in await check.refresh()] == [True]
    assert check.state()['latest'] == '9.0.0' and check.state()['update_available'] is True
    with pytest.raises(ValueError):
        await check.refresh()
    state = check.state()
    assert state['error'] == 'ValueError: GitHub releases response is not a list: {}'
    assert state['latest'] == '9.0.0'  # The previous answer stays visible next to the newer error.


def test_update_status_reports_version_and_check_without_network(tmp_path):
    def operator(request: Request) -> str:
        return 'operator'

    app = FastAPI()
    check = register_host_updates(app, root=tmp_path, user=operator, check_enabled=False)
    with TestClient(app) as client:
        body = client.get('/api/host/updates').json()
    assert check.enabled is False
    assert body['version'] == current_version() and body['managed'] is False and body['status'] is None
    assert body['check'] == {'enabled': False, 'current': current_version(), 'checked_at': None, 'error': None,
                             'latest': None, 'update_available': None, 'latest_prerelease': None}
