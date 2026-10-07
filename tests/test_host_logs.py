"""The host log file: one JSON line per record, correlation IDs, one redaction rule, readable back by the panel."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from len_bot.next.runtime.logs import (REDACTED, SECRETS, LoggingSettings, add_context, configure_logging, log_context,
                                       log_event, read_records, recorded_errors)


def _lines(directory):
    return [json.loads(line) for line in (directory / 'lenbot.jsonl').read_text(encoding='utf-8').splitlines()]


def test_records_carry_context_errors_and_no_credentials(tmp_path):
    settings = LoggingSettings(directory=tmp_path / 'logs')
    logger = logging.getLogger('len_bot.next.synthetic')
    with configure_logging(settings, ('synthetic-api-key',), console=False):
        SECRETS.add({'synthetic-plugin-secret'})
        with log_context(scene='onebot:group:80001', turn_id='turn-1'):
            with log_context(tool_call_id='call-1', tool='schedule', plugin='sample'):
                log_event(logger, 'tool_call', result_chars=3, url='https://x.invalid/?key=synthetic-api-key')
            try:
                raise ValueError('failed with synthetic-plugin-secret')
            except ValueError as error:
                log_event(logger, 'tool_failed', level=logging.WARNING, error=error)
            log_event(logger, 'task_finished', status='failed', error='recorded text')
        logger.info('plain record')
    first, second, third, fourth = _lines(settings.directory)
    assert first['event'] == 'tool_call' and first['scene'] == 'onebot:group:80001'
    assert (first['turn_id'], first['tool_call_id'], first['tool'], first['plugin']) == ('turn-1', 'call-1', 'schedule', 'sample')
    assert first['url'].endswith(REDACTED)
    assert 'tool_call_id' not in second and second['turn_id'] == 'turn-1'
    assert second['error']['type'] == 'ValueError' and REDACTED in second['error']['message']
    assert 'Traceback' in second['error']['traceback']
    assert third['error'] == {'message': 'recorded text'}
    assert 'turn_id' not in fourth and fourth['message'] == 'plain record'
    text = (settings.directory / 'lenbot.jsonl').read_text(encoding='utf-8')
    assert 'synthetic-api-key' not in text and 'synthetic-plugin-secret' not in text


@pytest.mark.asyncio
async def test_tasks_inherit_ids_and_added_ids_end_with_their_block(tmp_path):
    settings = LoggingSettings(directory=tmp_path / 'logs')
    logger = logging.getLogger('len_bot.next.synthetic')
    with configure_logging(settings, (), console=False):
        with log_context(scene='onebot:group:80001'):
            with log_context():
                add_context(turn_id='turn-2')
                await asyncio.create_task(asyncio.to_thread(lambda: None))
                await asyncio.create_task(_log(logger, 'inside'))
            log_event(logger, 'after')
    inside, after = _lines(settings.directory)
    assert inside['turn_id'] == 'turn-2' and inside['scene'] == 'onebot:group:80001'
    assert 'turn_id' not in after and after['scene'] == 'onebot:group:80001'


async def _log(logger, event):
    log_event(logger, event)


def test_panel_reads_newest_matching_records_and_restored_error_history(tmp_path):
    settings = LoggingSettings(directory=tmp_path / 'logs')
    logger = logging.getLogger('len_bot.next.synthetic')
    with configure_logging(settings, (), console=False):
        for number in range(3):
            with log_context(plugin='sample', turn_id=f'turn-{number}'):
                log_event(logger, 'plugin_error', level=logging.ERROR, error=RuntimeError(f'failure {number}'),
                          where='工具 lookup')
        log_event(logger, 'receipt', status='stored')
    newest = read_records(settings.directory, limit=2, match={'event': 'plugin_error'})
    assert [item['turn_id'] for item in newest] == ['turn-2', 'turn-1']
    assert [item['event'] for item in read_records(settings.directory, limit=10, level='ERROR')] == ['plugin_error'] * 3
    assert read_records(settings.directory, limit=10, match={'turn_id': 'turn-0'})[0]['error']['message'] == 'failure 0'
    history = recorded_errors(settings.directory, 'plugin_error', 'plugin', limit=2)
    assert [item['error'] for item in history['sample']] == ['RuntimeError: failure 1', 'RuntimeError: failure 2']
    assert history['sample'][0]['where'] == '工具 lookup'


def test_unknown_context_field_is_rejected():
    with pytest.raises(ValueError, match='unknown log context'):
        with log_context(user='onebot:70001'):
            pass
