"""Admission from actual settled costs and scene message records, without reservations."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .pricing import Rate
from .usage import usage
from .memory_jobs import processing_records


class ResourceLimits(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    daily_model_cost: Rate | None = None
    scene_daily_model_cost: dict[str, Rate] = Field(default_factory=dict)
    messages_per_hour: int | None = Field(default=60, ge=1)
    scene_messages_per_hour: dict[str, int | None] = Field(default_factory=dict)

    @field_validator('scene_daily_model_cost', 'scene_messages_per_hour')
    @classmethod
    def scenes(cls, values):
        import re
        if any(re.fullmatch(r'(group|private):[1-9][0-9]*', scene) is None for scene in values):
            raise ValueError('limits scene keys must be actual group/private QQ scenes')
        return values

    @field_validator('scene_messages_per_hour')
    @classmethod
    def counts(cls, values):
        if any(value is not None and value < 1 for value in values.values()):
            raise ValueError('hourly message limits must be positive or null')
        return values


class LimitReached(ValueError):
    def __init__(self, message: str, until: float):
        self.until = until
        super().__init__(message)


def day_window(now: float, timezone: str) -> tuple[float, float]:
    start = datetime.fromtimestamp(now, ZoneInfo(timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def check_speech(store, config) -> None:
    scene = config.scene
    if not scene.startswith('group:'):
        return
    limit = config.limits.scene_messages_per_hour.get(scene, config.limits.messages_per_hour)
    if limit is None:
        return
    start = (store.now() // 3600) * 3600
    count = store.db.execute(
        "SELECT COUNT(*) FROM messages WHERE scene=? AND json_extract(body,'$.is_self')=1 "
        "AND json_extract(body,'$.time')>=? AND json_extract(body,'$.time')<? "
        "AND json_extract(body,'$.send_status') IN ('sent','unconfirmed','simulated','received')",
        (scene, start, start + 3600),
    ).fetchone()[0]
    if count >= limit:
        raise LimitReached(f'本群本小时发言已达 {limit} 条，暂停发言。', start + 3600)


class ModelBudget:
    def __init__(self, config, store, memory, *, root: Path | None = None):
        self.config, self.store, self.memory = config, store, memory
        self.started_at = store.now()
        self.trials_root: Path | None = None if root is None else root / '.runtime' / 'chat-tests'
        if config.limits.daily_model_cost is not None or config.limits.scene_daily_model_cost:
            self.validate_sources()

    def validate_sources(self) -> None:
        from .memory_jobs import FORMAT_VERSION as MEMORY_FORMAT
        paths = [self.config.database.with_name(self.config.database.name + '.memory.sqlite3')]
        if self.trials_root is not None:
            paths.extend(self.trials_root.glob('*/state.db.memory.sqlite3'))
        from .store import FORMAT_VERSION as BUSINESS_FORMAT
        if self.trials_root is not None:
            for path in self.trials_root.glob('*/state.db'):
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    app = db.execute('PRAGMA application_id').fetchone()[0]
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424E31 or version != BUSINESS_FORMAT:
                        raise ValueError(f'试聊计量源需要停机离线迁移：{path} (application={app}, format={version})；'
                                         '运行 python -m len_bot.next.migrate')
        outdated = []
        for path in paths:
            if path.exists():
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    app, version = db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424D4A or version != MEMORY_FORMAT:
                        outdated.append(f'{path} (application={app}, format={version})')
        if outdated:
            raise ValueError('预算计量源需要停机离线迁移；运行 python -m len_bot.next.migrate_memory_jobs：' + '; '.join(outdated))

    def totals(self, scene: str | None, since: float, until: float) -> dict:
        selected = None if scene is None else [scene]
        def sample(store, memory=None, memory_db=None):
            result = usage(store, selected, since, until, memory=memory, memory_db=memory_db)
            if result['unfinished_calls']:
                old = usage(store, selected, since, min(until, self.started_at), memory=memory, memory_db=memory_db)
                result['settled_unknown_calls'] += old['unfinished_calls']
            return result
        with processing_records(self.config.database, None if self.memory is None else self.memory.jobs) as records:
            samples = [sample(self.store, memory=self.memory, memory_db=None if records is None else records.db)]
        if self.trials_root is not None:
            for path in self.trials_root.glob('*/state.db'):
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    with processing_records(path, None) as records:
                        samples.append(sample(SimpleNamespace(db=db), memory_db=None if records is None else records.db))
        amounts = {}
        for sample in samples:
            for currency, amount in sample['known_amounts'].items():
                amounts[currency] = amounts.get(currency, Decimal(0)) + Decimal(amount)
        return {'known_amounts': amounts,
                'settled_unknown_calls': sum(sample['settled_unknown_calls'] + sample['unverified_summary_attempts'] for sample in samples),
                'unfinished_calls': sum(sample['unfinished_calls'] for sample in samples)}

    def check(self, scene: str | None) -> None:
        limits = self.config.limits
        checks = [(None, limits.daily_model_cost, self.config.timezone)]
        if scene in limits.scene_daily_model_cost:
            checks.append((scene, limits.scene_daily_model_cost[scene], self.config.scene_timezone(scene)))
        for scope, cap, timezone in checks:
            if cap is None:
                continue
            since, until = day_window(self.store.now(), timezone)
            result = self.totals(scope, since, until)
            label = '全局' if scope is None else '本场景'
            if result['settled_unknown_calls'] or set(result['known_amounts']) - {limits.currency}:
                raise LimitReached(f'{label}今日存在费用未知、历史计量不全或不同币种的调用，暂停模型请求；请核对计量。', until)
            if result['known_amounts'].get(limits.currency, Decimal(0)) >= cap:
                raise LimitReached(f'{label}今日模型金额已达 {cap} {limits.currency}，暂停模型请求。', until)
