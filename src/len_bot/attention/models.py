from enum import StrEnum
from typing import Optional, Any
from pydantic import BaseModel, Field

class AttentionDisposition(StrEnum):
    DROP = "DROP"
    OBSERVE = "OBSERVE"
    TRACK = "TRACK"
    WAKE = "WAKE"

class AttentionResult(BaseModel):
    disposition: AttentionDisposition
    reason: str
    soft_annotation: Optional[dict[str, Any]] = None
