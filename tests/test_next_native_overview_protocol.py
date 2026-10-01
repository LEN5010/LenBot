"""Native overview/reindex formats and de-identified session-commit task responses."""

import pytest

from len_bot.next.memory_overview import parse_overview, parse_refresh
from len_bot.next.memory_openviking import parse_ingest_task

URI = 'viking://user/synthetic-group/memories'
DOCUMENT = '''---
directory: viking://user/synthetic-group/memories/
freshness:
  total_entries: 3
  sampled_entries: 2
  unsampled_entries: 1
  pending_child_changes: 1
  missing_summary_entries: 1
---

合成场景：周六活动。
'''


def test_native_overview_preserves_actual_incomplete_freshness():
    result = parse_overview(DOCUMENT, uri=URI, path='memories').as_dict()
    assert result['content'] == '合成场景：周六活动。\n'
    assert result['freshness'] == dict(total_entries=3, sampled_entries=2,
                                      unsampled_entries=1, pending_child_changes=1,
                                      missing_summary_entries=1)
    assert result['coverage'] == 'direct_children'


def test_missing_freshness_remains_unknown():
    document = f'---\ndirectory: {URI}\n---\n合成概览'
    assert parse_overview(document, uri=URI, path='memories').freshness is None


@pytest.mark.parametrize('document', [
    'Directory overview is not ready',
    DOCUMENT.replace('total_entries: 3', 'total_entries: true'),
    DOCUMENT.replace('total_entries: 3', 'total_entries: 4'),
    DOCUMENT.replace('pending_child_changes: 1', 'pending_child_changes: -1'),
    DOCUMENT.replace('synthetic-group', 'another-group'),
    DOCUMENT.replace('---\n\n', ''),
    '---\n[1, 2]\n---\ntext',
])
def test_invalid_native_overview_reports_original_input(document):
    with pytest.raises(ValueError, match='raw='):
        parse_overview(document, uri=URI, path='memories')


def receipt():
    return dict(status='completed', uri=URI, object_type='memory',
                mode='semantic_and_vectors', scanned_records=3, rebuilt_records=2,
                deleted_records=0, unsupported_records=0, failed_records=1,
                duration_ms=20, warnings=['synthetic embedding failure'])


def test_refresh_completed_preserves_partial_failure():
    result = parse_refresh(receipt(), uri=URI, raw='synthetic raw response')
    assert result.failed_records == 1
    assert result.warnings == ['synthetic embedding failure']


@pytest.mark.parametrize(('field', 'value'), [('uri', URI + '/other'), ('mode', 'vectors_only'),
                                             ('failed_records', True), ('status', 'queued')])
def test_invalid_refresh_receipt_fails_at_boundary(field, value):
    row = receipt()
    row[field] = value
    with pytest.raises(ValueError, match='synthetic raw response'):
        parse_refresh(row, uri=URI, raw='synthetic raw response')


def commit_task():
    """Relevant fields of an observed completed task; identities and URIs are synthetic."""
    return dict(task_id='synthetic-task', task_type='session_commit', resource_id='synthetic-session',
                status='completed', error=None,
                result=dict(session_id='synthetic-session',
                            archive_uri='viking://user/synthetic-group/sessions/synthetic-session/history/archive_001',
                            memories_extracted={'memory_edit': 3}, session_skills_extracted=0,
                            session_skill_uris=[], usage_events_extracted=0,
                            agent_evolution_enabled=False))


@pytest.mark.parametrize('counts', [{'memory_edit': 3}, {}])
def test_native_commit_preserves_category_counts_and_total(counts):
    row = commit_task()
    row['result']['memories_extracted'] = counts
    task = parse_ingest_task(row, task_id='synthetic-task', raw='de-identified completed response')
    assert task.status == 'completed'
    assert task.memories_extracted == counts
    assert task.memories_extracted_total == sum(counts.values())
    assert task.result == row['result']


def test_native_commit_missing_count_stays_unknown():
    row = commit_task()
    del row['result']['memories_extracted']
    task = parse_ingest_task(row, task_id='synthetic-task', raw='de-identified missing-count response')
    assert task.memories_extracted is None
    assert task.memories_extracted_total is None


@pytest.mark.parametrize('counts', [3, [], None, {'memory_edit': True}, {'memory_edit': -1},
                                   {'memory_edit': 1.5}, {'memory_edit': '3'}])
def test_native_commit_invalid_count_reports_original_input(counts):
    row = commit_task()
    row['result']['memories_extracted'] = counts
    with pytest.raises(ValueError, match='de-identified invalid-count response'):
        parse_ingest_task(row, task_id='synthetic-task', raw='de-identified invalid-count response')


@pytest.mark.parametrize(('field', 'value'), [('task_id', 'another-task'), ('task_type', 'reindex'),
                                             ('status', 'unknown')])
def test_native_commit_invalid_task_reports_original_input(field, value):
    row = commit_task()
    row[field] = value
    with pytest.raises(ValueError, match='de-identified invalid-task response'):
        parse_ingest_task(row, task_id='synthetic-task', raw='de-identified invalid-task response')
