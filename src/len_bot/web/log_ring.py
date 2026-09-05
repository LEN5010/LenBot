"""In-memory log ring for the Control Plane 'Logs' view (ADR-0022).

Runtime operational logs are distinct from immutable domain Events (§三十):
a logging handler keeps the last N records so the UI can filter by level.
"""

import logging
import time
from collections import deque


class LogRingBuffer(logging.Handler):
    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.buffer: deque[dict] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(name)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.append({
            "timestamp": time.time(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage()[:500],
        })

    def snapshot(self, level: str | None = None, limit: int = 200) -> list[dict]:
        rows = list(self.buffer)[::-1]
        if level:
            rows = [r for r in rows if r["level"] == level.upper()]
        return rows[:limit]

    def clear(self) -> None:
        self.buffer.clear()
