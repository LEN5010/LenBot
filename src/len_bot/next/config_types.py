"""Common scalar configuration types and parsing at the root boundary."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, ConfigDict, Field


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _epoch_seconds(seconds: float) -> float:
    try:
        datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("Unix seconds are outside the representable UTC date range") from error
    return seconds


def _valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown timezone {value!r}") from error
    return value


EpochSeconds = Annotated[float, Field(strict=True, allow_inf_nan=False), AfterValidator(_epoch_seconds)]


_FiniteSeconds = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _valid_scene(value: str) -> str:
    if re.fullmatch(r"(?:group|private):[1-9][0-9]*", value) is None:
        raise ValueError("must be group:<QQ> or private:<QQ>")
    return value
