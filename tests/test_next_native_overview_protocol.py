"""Native overview wire format, matching storage/abstract_overview.py and reindex_executor.py."""

import pytest

from len_bot.next.memory_overview import parse_overview, parse_refresh

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
