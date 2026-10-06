"""Admission from actual reported tokens and scene message records, without reservations."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .usage import usage
from ..memory.jobs import processing_records


class ResourceLimits(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    # Input plus output tokens per local day, counted from what the model services reported.
    daily_tokens: int | None = Field(default=None, gt=0)
    scene_daily_tokens: dict[str, int] = Field(default_factory=dict)
    messages_per_hour: int | None = Field(default=60, ge=1)
    scene_messages_per_hour: dict[str, int | None] = Field(default_factory=dict)

    @field_validator('scene_daily_tokens', 'scene_messages_per_hour')
    @classmethod
    def scenes(cls, values):
        import re
        if any(re.fullmatch(r'[a-z][a-z0-9_-]*:(group|private):[^:\s/\\]+', scene) is None for scene in values):
            raise ValueError('limits scene keys must be actual platform group/private scenes')
        return values

    @field_validator('scene_daily_tokens')
    @classmethod
    def positive_tokens(cls, values):
        if any(value < 1 for value in values.values()):
            raise ValueError('daily token limits must be positive')
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
    if scene.split(':', 2)[1] != 'group':
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
        if config.limits.daily_tokens is not None or config.limits.scene_daily_tokens:
            self.validate_sources()

    def validate_sources(self) -> None:
        from ..memory.jobs import FORMAT_VERSION as MEMORY_FORMAT
        paths = [self.config.database.with_name(self.config.database.name + '.memory.sqlite3')]
        if self.trials_root is not None:
            paths.extend(self.trials_root.glob('*/state.db.memory.sqlite3'))
        from ..storage.store import FORMAT_VERSION as BUSINESS_FORMAT
        if self.trials_root is not None:
            for path in self.trials_root.glob('*/state.db'):
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    app = db.execute('PRAGMA application_id').fetchone()[0]
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424E31 or version != BUSINESS_FORMAT:
                        raise ValueError(f'试聊计量源需要停机离线迁移：{path} (application={app}, format={version})；'
                                         '运行 python -m len_bot.next.maintenance.migrate')
        outdated = []
        for path in paths:
            if path.exists():
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    app, version = db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424D4A or version != MEMORY_FORMAT:
                        outdated.append(f'{path} (application={app}, format={version})')
        if outdated:
            raise ValueError('预算计量源需要停机离线迁移；运行 python -m len_bot.next.maintenance.migrate_memory_jobs：' + '; '.join(outdated))

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
        return {'tokens': sum(sample['budgeted']['input'] + sample['budgeted']['output'] for sample in samples),
                'settled_unknown_calls': sum(sample['settled_unknown_calls'] + sample['unverified_summary_attempts'] for sample in samples),
                'unfinished_calls': sum(sample['unfinished_calls'] for sample in samples)}

    def check(self, scene: str | None) -> None:
        limits = self.config.limits
        checks = [(None, limits.daily_tokens, self.config.timezone)]
        if scene in limits.scene_daily_tokens:
            checks.append((scene, limits.scene_daily_tokens[scene], self.config.scene_timezone(scene)))
        for scope, cap, timezone in checks:
            if cap is None:
                continue
            since, until = day_window(self.store.now(), timezone)
            result = self.totals(scope, since, until)
            label = '全局' if scope is None else '本场景'
            if result['settled_unknown_calls']:
                raise LimitReached(f'{label}今日有模型调用没有报告 token，或历史计量不全，暂停模型请求；请核对计量。', until)
            if result['tokens'] >= cap:
                raise LimitReached(f'{label}今日模型 token 已达 {cap}，暂停模型请求。', until)
