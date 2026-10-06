"""Offline qualification of existing reminders without changing their execution state."""

from len_bot.next.chat.schedule_store import ScheduleStore
from len_bot.next.maintenance.migrate import migrate
from len_bot.next.storage.store import FORMAT_VERSION, Store


def test_schedule_target_upgrade_preserves_reminders_and_is_repeatable(tmp_path):
    path = tmp_path / 'state.db'
    with Store(path) as store:
        records = ScheduleStore(store)
        for scene, target in [('onebot:group:80001', '70001'),
                              ('onebot:private:70002', '70002'),
                              ('onebot:group:80001', 'self'),
                              ('onebot:group:80001', 'onebot:70003')]:
            records.create_schedule(scene, due_at=1800000000.0, timezone='UTC',
                                    note='保留提醒原文', target=target, requester='onebot:70001',
                                    limit=50, interval_seconds=3600)
        records.block_schedule('onebot:private:70002', 2, '原有权限阻止原因')
        before = store.db.execute('SELECT * FROM schedules ORDER BY id').fetchall()
        store.db.execute('PRAGMA user_version=2')

    migrate(path)
    migrate(path)
    with Store(path) as store:
        after = store.db.execute('SELECT * FROM schedules ORDER BY id').fetchall()
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == FORMAT_VERSION
        assert [row['target'] for row in after] == ['onebot:70001', 'onebot:70002', 'self', 'onebot:70003']
        for original, upgraded in zip(before, after, strict=True):
            assert {key: original[key] for key in original.keys() if key != 'target'} == {
                key: upgraded[key] for key in upgraded.keys() if key != 'target'}
