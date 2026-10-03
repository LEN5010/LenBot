"""Bounded in-memory projection of one task's current native assistant text."""

from __future__ import annotations


MAX_PREVIEW_CHARACTERS = 16000


class TaskLiveText:
    def __init__(self) -> None:
        self._blocks: dict[int, str] = {}
        self._cut: set[int] = set()
        self._overflow = False
        self._active = False
        self._complete = False

    def snapshot(self) -> dict | None:
        if not self._active:
            return None
        pieces: list[str] = []
        remaining = MAX_PREVIEW_CHARACTERS
        truncated = bool(self._cut) or self._overflow
        for index, block in sorted(self._blocks.items()):
            if pieces:
                if remaining:
                    pieces.append("\n")
                    remaining -= 1
                else:
                    truncated = True
            if len(block) > remaining:
                truncated = True
            pieces.append(block[:remaining])
            remaining -= min(len(block), remaining)
        return {"text": "".join(pieces), "truncated": truncated, "complete": self._complete}

    def start(self, message: dict) -> bool:
        was_active = self._active
        self._blocks.clear()
        self._cut.clear()
        self._overflow = False
        self._active = message["role"] == "assistant"
        self._complete = False
        return was_active or self._active

    def update(self, event: dict) -> bool:
        if not self._active or event["type"] not in {"text_start", "text_delta", "text_end"}:
            return False
        index = event["contentIndex"]
        if event["type"] == "text_delta" and index in self._cut:
            return False
        if index not in self._blocks and len(self._blocks) >= MAX_PREVIEW_CHARACTERS:
            changed = not self._overflow
            self._overflow = True
            return changed
        if event["type"] == "text_start":
            self._blocks[index] = ""
            self._cut.discard(index)
        else:
            prior = self._blocks.get(index, "")
            value = prior + event["delta"] if event["type"] == "text_delta" else event["content"]
            budget = max(0, MAX_PREVIEW_CHARACTERS - sum(
                len(block) for other, block in self._blocks.items() if other != index
            ))
            self._blocks[index] = value[:budget]
            if len(value) > budget:
                self._cut.add(index)
            elif event["type"] == "text_end":
                self._cut.discard(index)
        return True

    def finish(self, message: dict) -> bool:
        if message["role"] != "assistant":
            return False
        self._active = True
        self._complete = True
        self._blocks.clear()
        self._cut.clear()
        self._overflow = False
        remaining = MAX_PREVIEW_CHARACTERS
        for index, part in enumerate(part for part in message["content"] if part["type"] == "text"):
            if index >= MAX_PREVIEW_CHARACTERS:
                self._overflow = True
                break
            if index and remaining:
                remaining -= 1
            text = part["text"]
            if len(text) > remaining:
                self._cut.add(index)
            self._blocks[index] = text[:remaining]
            remaining -= min(len(text), remaining)
        return True
