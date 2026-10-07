"""The host's one log: JSON Lines with correlation IDs, one redaction rule, one error shape.

Every component logs through the standard ``logging`` module. ``configure_logging``
installs a daily-rotated ``lenbot.jsonl`` file and a short console line on stderr.
``log_context`` binds the IDs of the work in progress (scene, turn, tool call, plugin,
task, ...); asyncio tasks inherit them, so a record written anywhere below carries them.
The database keeps business state; following one message across components uses the log.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import re
import sys
import traceback
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LOG_NAME = 'lenbot.jsonl'
REDACTED = '[credential removed]'
CONTEXT_FIELDS = ('scene', 'message_seq', 'platform_message_id', 'turn_id', 'tool_call_id', 'tool',
                  'plugin', 'task_id', 'job')
# Third-party loggers that report every request at INFO.
QUIET_LOGGERS = ('httpx', 'httpcore', 'uvicorn.access', 'websockets', 'docker', 'urllib3')

_context: ContextVar[dict[str, object]] = ContextVar('lenbot_log_context', default={})


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    directory: Path = Path('logs')
    retention_days: int = Field(default=14, ge=1, le=3650)
    level: Literal['INFO', 'WARNING', 'ERROR'] = 'INFO'


@contextmanager
def log_context(**ids: object) -> Iterator[None]:
    """Bind correlation IDs for everything logged inside, including tasks started inside."""
    unknown = set(ids) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f'unknown log context fields: {sorted(unknown)}')
    token = _context.set({**_context.get(), **{key: value for key, value in ids.items() if value is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def add_context(**ids: object) -> None:
    """Add IDs for the rest of the enclosing ``log_context`` block, which restores the previous IDs on exit."""
    unknown = set(ids) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f'unknown log context fields: {sorted(unknown)}')
    _context.set({**_context.get(), **{key: value for key, value in ids.items() if value is not None}})


def current_context() -> dict[str, object]:
    return dict(_context.get())


def error_text(error: BaseException) -> str:
    """The one-line form stored in database error columns and shown to models."""
    return f'{type(error).__name__}: {error}'


def error_fields(error: BaseException) -> dict[str, str]:
    return {'type': type(error).__name__, 'message': str(error),
            'traceback': ''.join(traceback.format_exception(error))}


def credentials(config) -> tuple[str, ...]:
    """Explicit configuration credential fields, including arbitrary MCP headers; a parsed config or its raw JSON."""
    found: set[str] = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'api_key', 'access_token', 'password', 'password_hash'} and isinstance(item, str) and item:
                    found.add(item)
                elif key in {'headers', 'env'} and isinstance(item, dict):
                    found.update(v for v in item.values() if isinstance(v, str) and v)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(config if isinstance(config, dict) else config.model_dump(mode='json'))
    return tuple(sorted(found, key=len, reverse=True))


def redact(text: str, secrets: tuple[str, ...] | set[str], *, identities: bool = False) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
        text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], REDACTED)
    text = re.sub(r'(?i)(authorization["\s:=]+(?:bearer\s+)?)[^\s",}]+', r'\1' + REDACTED, text)
    if identities:
        # Export-only masking; no stable identity aliases or replacement identities.
        text = re.sub(r'(?<!\d)\d{5,}(?!\d)', '[number removed]', text)
    return text


def redact_record(value, clean):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [redact_record(item, clean) for item in value]
    if isinstance(value, dict):
        return {key: redact_record(item, clean) for key, item in value.items()}
    return value


class Secrets:
    """Process-wide values removed from every log line: configuration credentials and plugin secrets."""

    def __init__(self) -> None:
        self.values: set[str] = set()

    def add(self, values) -> None:
        self.values.update(value for value in values if isinstance(value, str) and value)

    def clean(self, text: str) -> str:
        return redact(text, self.values)


SECRETS = Secrets()


class _Context(logging.Filter):
    """Capture the correlation IDs where the record is created, before any handler thread."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'context'):
            record.context = current_context()
        return True


def record_entry(record: logging.LogRecord) -> dict:
    entry: dict[str, object] = {
        'ts': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
        'level': record.levelname, 'source': record.name,
    }
    event = getattr(record, 'event', None)
    if event is not None:
        entry['event'] = event
    entry['message'] = record.getMessage()
    entry.update(getattr(record, 'context', {}))
    entry.update(getattr(record, 'fields', {}))
    if record.exc_info and record.exc_info[1] is not None:
        entry['error'] = error_fields(record.exc_info[1])
    return entry


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return SECRETS.clean(json.dumps(record_entry(record), ensure_ascii=False, default=str))


class ConsoleFormatter(logging.Formatter):
    """A short local line; the JSON file is the complete record."""

    def format(self, record: logging.LogRecord) -> str:
        entry = record_entry(record)
        ids = ' '.join(f'{key}={entry[key]}' for key in CONTEXT_FIELDS if key in entry)
        error = entry.get('error')
        line = ' '.join(part for part in (
            entry['ts'][11:19], record.levelname, record.name, str(entry.get('event') or ''),
            '' if entry['message'] == entry.get('event') else entry['message'], ids,
            '' if error is None else f'{error["type"]}: {error["message"]}') if part)
        return SECRETS.clean(line)


def log_event(logger: logging.Logger, event: str, message: str = '', *, level: int = logging.INFO,
              error: BaseException | str | None = None, **fields: object) -> None:
    """One structured record; ``fields`` are JSON values next to the correlation IDs.

    ``error`` is an exception (type, message and traceback are kept) or an already recorded
    error text; both appear as the record's ``error`` object.
    """
    if isinstance(error, str):
        fields['error'] = {'message': error}
        error = None
    logger.log(level, message or event, extra={'event': event, 'fields': fields},
               exc_info=None if error is None else (type(error), error, error.__traceback__))


def log_files(directory: Path) -> list[Path]:
    """The current file first, then rotated days, newest first."""
    current = directory / LOG_NAME
    rotated = sorted((path for path in directory.glob(LOG_NAME + '.*')
                      if re.fullmatch(re.escape(LOG_NAME) + r'\.\d{4}-\d{2}-\d{2}', path.name)), reverse=True)
    return ([current] if current.exists() else []) + rotated


def read_records(directory: Path, *, limit: int, match: dict[str, str] | None = None,
                 level: str | None = None) -> list[dict]:
    """Newest matching records; ``match`` compares string forms of top-level fields."""
    levels = {'INFO': 20, 'WARNING': 30, 'ERROR': 40}
    found: list[dict] = []
    for path in log_files(directory):
        for line in reversed(path.read_text(encoding='utf-8').splitlines()):
            entry = json.loads(line)
            if level is not None and logging.getLevelName(entry['level']) < levels[level]:
                continue
            if match and any(str(entry.get(key)) != value for key, value in match.items()):
                continue
            found.append(entry)
            if len(found) >= limit:
                return found
    return found


def recorded_errors(directory: Path, event: str, owner: str, *, limit: int = 20) -> dict[str, list[dict]]:
    """Errors logged under ``event`` before this process, per ``owner`` field value, oldest first.

    Seeds the in-memory error lists of plugins and MCP services so a restart keeps their history.
    """
    found: dict[str, list[dict]] = {}
    for entry in read_records(directory, limit=1000, match={'event': event}):
        name = entry.get(owner)
        items = found.setdefault(str(name), [])
        if name is None or len(items) >= limit:
            continue
        error = entry.get('error') or {}
        items.append({'at': datetime.fromisoformat(entry['ts']).timestamp(), 'where': entry.get('where'),
                      'error': f"{error.get('type', 'Error')}: {error.get('message', '')}"})
    return {name: list(reversed(items)) for name, items in found.items()}


@contextmanager
def configure_logging(settings: LoggingSettings, secrets: tuple[str, ...], *, console: bool = True) -> Iterator[None]:
    """Install the JSON file and console handlers on the root logger for the host's lifetime."""
    settings.directory.mkdir(parents=True, exist_ok=True)
    SECRETS.add(secrets)
    context = _Context()
    handlers: list[logging.Handler] = []
    file = TimedRotatingFileHandler(settings.directory / LOG_NAME, when='midnight', utc=True,
                                    backupCount=settings.retention_days, encoding='utf-8')
    file.setFormatter(JsonFormatter())
    handlers.append(file)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(ConsoleFormatter())
        handlers.append(stream)
    root = logging.getLogger()
    previous = root.level, {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    root.setLevel(settings.level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for handler in handlers:
        handler.addFilter(context)
        root.addHandler(handler)
    try:
        yield
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()
        root.setLevel(previous[0])
        for name, level in previous[1].items():
            logging.getLogger(name).setLevel(level)


def run_maintenance(main: Callable[[], None], command: str, root: Path | None = None) -> None:
    """Run one stopped-instance command with its start, end and failure in the instance's log.

    The configuration is read as raw JSON, because migrations run before it can be parsed by
    the current code; the command's own terminal output is unchanged.
    """
    root = (Path.cwd() if root is None else root).resolve()
    path = root / 'lenbot.config.json'
    source = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    logs = source.get('logging') if isinstance(source.get('logging'), dict) else {}
    directory = Path(logs.get('directory', 'logs'))
    settings = LoggingSettings(directory=directory if directory.is_absolute() else root / directory)
    logger = logging.getLogger(f'len_bot.maintenance.{command}')
    with configure_logging(settings, credentials(source), console=False):
        log_event(logger, 'maintenance_started', arguments=sys.argv[1:])
        try:
            main()
        except SystemExit as exit:
            log_event(logger, 'maintenance_finished', level=logging.INFO if not exit.code else logging.ERROR,
                      exit_code=exit.code)
            raise
        except BaseException as error:
            log_event(logger, 'maintenance_failed', level=logging.ERROR, error=error)
            raise
        log_event(logger, 'maintenance_finished', exit_code=0)
