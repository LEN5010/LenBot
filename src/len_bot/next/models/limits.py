"""Admission from actual reported tokens and recorded scene expressions, without reservations."""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .usage import instance_calls, summarize_calls
from ..storage.sqlite import connect


class ResourceLimits(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    # Input plus output tokens per local day, counted from what the model services reported.
    daily_tokens: int | None = Field(default=None, gt=0)
    scene_daily_tokens: dict[str, int] = Field(default_factory=dict)
    # Expressions per group in the last 60 minutes; a split message or one with a sticker counts once.
    messages_per_hour: int | None = Field(default=60, ge=1)
    scene_messages_per_hour: dict[str, int | None] = Field(default_factory=dict)
    # Extra expressions for direct wakes and reminders after the normal limit is used up.
    direct_reserve: int = Field(default=5, ge=0)
    # Sent once per blocked window when a direct wake arrives with nothing left; not counted.
    speech_notice_text: str | None = Field(default=None, max_length=200)

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

    @field_validator('speech_notice_text')
    @classmethod
    def nonblank_notice(cls, value):
        if value is not None and not value.strip():
            raise ValueError('speech_notice_text must be nonblank or null')
        return value

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


SPEECH_WINDOW = 3600
SPEECH_FIELDS = ('messages_per_hour', 'scene_messages_per_hour', 'direct_reserve', 'speech_notice_text')


class SpeechLimitReached(LimitReached):
    """The scene's hourly expressions are used up; ``direct`` tells whether the reserve is gone too."""
    def __init__(self, message: str, until: float, *, direct: bool):
        self.direct = direct
        super().__init__(message, until)


@dataclass(frozen=True)
class SpeechQuota:
    source: Literal['global', 'scene', 'unlimited', 'private']
    limit: int | None
    reserve: int
    used: int
    times: tuple[float, ...]

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)

    @property
    def reserve_left(self) -> int:
        if self.limit is None:
            return self.reserve
        return max(0, min(self.reserve, self.limit + self.reserve - self.used))

    def blocked(self, *, direct: bool) -> bool:
        return self.limit is not None and self.used >= self.limit + (self.reserve if direct else 0)

    def until(self, *, direct: bool) -> float | None:
        """When enough old expressions leave the window for one more."""
        if not self.blocked(direct=direct):
            return None
        return self.times[self.used - self.limit - (self.reserve if direct else 0)] + SPEECH_WINDOW


def speech_quota(store, config, scene: str | None = None) -> SpeechQuota:
    scene = config.scene if scene is None else scene
    limits = config.limits
    if scene.split(':', 2)[1] != 'group':
        return SpeechQuota('private', None, limits.direct_reserve, 0, ())
    if scene in limits.scene_messages_per_hour:
        limit = limits.scene_messages_per_hour[scene]
        source = 'scene' if limit is not None else 'unlimited'
    else:
        limit = limits.messages_per_hour
        source = 'global' if limit is not None else 'unlimited'
    times = tuple(row[0] for row in store.db.execute(
        "SELECT time FROM speech WHERE scene=? AND time>? ORDER BY time", (scene, store.now() - SPEECH_WINDOW)))
    return SpeechQuota(source, limit, limits.direct_reserve, len(times), times)


def check_speech(store, config, *, direct: bool = False) -> None:
    quota = speech_quota(store, config)
    if quota.blocked(direct=direct):
        text = (f'本群最近一小时已发言 {quota.used} 条，被叫到时的余量也已用完，暂停发言。' if direct and quota.reserve
                else f'本群最近一小时已发言 {quota.used} 条，达到上限 {quota.limit} 条，暂停发言。')
        raise SpeechLimitReached(text, quota.until(direct=direct), direct=direct)


def record_speech(store, scene: str) -> None:
    """One expression that reached the platform or the simulation, whatever its number of parts."""
    if scene.split(':', 2)[1] != 'group':
        return
    now = store.now()
    with store.db:
        store.db.execute("DELETE FROM speech WHERE scene=? AND time<=?", (scene, now - SPEECH_WINDOW))
        store.db.execute("INSERT INTO speech(scene,time) VALUES (?,?)", (scene, now))


def clear_speech(store, scene: str) -> None:
    """Forget the current window so the scene may speak again at once."""
    with store.db:
        store.db.execute("DELETE FROM speech WHERE scene=?", (scene,))


def apply_speech_limits(running, saved) -> None:
    """Speech limits are read at every check; take the saved ones without a restart."""
    for name in SPEECH_FIELDS:
        setattr(running.limits, name, getattr(saved.limits, name))


class ModelBudget:
    def __init__(self, config, store, memory, *, root: Path | None = None):
        self.config, self.store, self.memory = config, store, memory
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
                with closing(connect(path, readonly=True)) as db:
                    app = db.execute('PRAGMA application_id').fetchone()[0]
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424E31 or version != BUSINESS_FORMAT:
                        raise ValueError(f'试聊计量源需要停机离线迁移：{path} (application={app}, format={version})；'
                                         '运行 python -m len_bot.next.maintenance.migrate')
        outdated = []
        for path in paths:
            if path.exists():
                with closing(connect(path, readonly=True)) as db:
                    app, version = db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0]
                    if app != 0x4C424D4A or version != MEMORY_FORMAT:
                        outdated.append(f'{path} (application={app}, format={version})')
        if outdated:
            raise ValueError('预算计量源需要停机离线迁移；运行 python -m len_bot.next.maintenance.migrate_memory_jobs：' + '; '.join(outdated))

    def totals(self, scene: str | None, since: float, until: float) -> dict:
        selected = None if scene is None else [scene]
        calls = instance_calls(self.store, self.config.database, selected, since, until,
                               memory=self.memory, trials_root=self.trials_root)
        result = summarize_calls(calls, since, until)
        return {'tokens': result['budgeted']['input'] + result['budgeted']['output'],
                'settled_unknown_calls': result['settled_unknown_calls'],
                'successful_unknown_calls': result['successful_unknown_calls'],
                'unfinished_calls': result['unfinished_calls']}

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
            if result['successful_unknown_calls']:
                raise LimitReached(f'{label}今日有成功模型调用没有报告 token，暂停模型请求；请核对计量。', until)
            if result['tokens'] >= cap:
                raise LimitReached(f'{label}今日模型 token 已达 {cap}，暂停模型请求。', until)
