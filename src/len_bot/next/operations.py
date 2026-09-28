"""Host log files and portable, redacted diagnostic data; no extra event store."""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import re
import time
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    directory: Path
    retention_days: int = Field(default=14, ge=1, le=3650)
    level: Literal["INFO", "WARNING", "ERROR"] = "INFO"


def credentials(config) -> tuple[str, ...]:
    """Explicit configuration credential fields, including arbitrary MCP headers."""
    found: set[str] = set()
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"api_key", "access_token", "password", "password_hash"} and isinstance(item, str) and item:
                    found.add(item)
                elif key in {"headers", "env"} and isinstance(item, dict):
                    found.update(v for v in item.values() if isinstance(v, str) and v)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(config.model_dump(mode="json"))
    return tuple(sorted(found, key=len, reverse=True))


def redact_record(value, clean):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [redact_record(item, clean) for item in value]
    if isinstance(value, dict):
        return {key: redact_record(item, clean) for key, item in value.items()}
    return value


def redact(text: str, secrets: tuple[str, ...], *, identities: bool = False) -> str:
    for secret in secrets:
        text = text.replace(secret, "[credential removed]")
        text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[credential removed]")
    text = re.sub(r'(?i)(authorization["\s:=]+(?:bearer\s+)?)[^\s",}]+', r'\1[credential removed]', text)
    if identities:
        # Export-only masking; no stable identity aliases or replacement identities.
        text = re.sub(r"(?<!\d)\d{5,}(?!\d)", "[number removed]", text)
    return text


@contextmanager
def host_logging(settings: LoggingSettings | None, secrets: tuple[str, ...]):
    if settings is None:
        yield
        return
    settings.directory.mkdir(parents=True, exist_ok=True)
    class Formatter(logging.Formatter):
        def format(self, record):
            return redact(super().format(record), secrets)
    handler = TimedRotatingFileHandler(settings.directory / "host.log", when="midnight", utc=True,
                                       backupCount=settings.retention_days, encoding="utf-8")
    handler.setFormatter(Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s"))
    handler.formatter.converter = time.gmtime
    logger = logging.getLogger("len_bot.next")
    previous = logger.level
    logger.setLevel(settings.level)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def diagnostic_value(value):
    """Exclude known multimodal payloads from the text diagnostic projection."""
    if isinstance(value, list):
        return [diagnostic_value(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url", "input_image", "input_audio"}:
            return {"type": value["type"], "omitted": "binary media excluded from diagnostic export"}
        return {key: diagnostic_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return re.sub(r"(?:data:[^\s;,]+;base64,|base64://)[A-Za-z0-9+/=]+",
                      "[binary media omitted]", value)
    return value


def diagnostic_zip(payload: dict, config, *, plugin_redactor=None) -> bytes:
    text = json.dumps(diagnostic_value(payload), ensure_ascii=False, indent=2, allow_nan=False)
    if plugin_redactor is not None:
        text = plugin_redactor(text)
    text = redact(text, credentials(config), identities=True)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("diagnostic.txt", text)
        archive.writestr("README.txt", "诊断资料，不是可直接回放的评测快照。已隐去配置凭据与至少5位的数字（可能同时遮盖时间、消息号等）。\n"
                         "正文仍可能含昵称、私人事实和第三方返回内容；附件、原图、语音及文件原件不包含在此包中。\n")
    return buffer.getvalue()
