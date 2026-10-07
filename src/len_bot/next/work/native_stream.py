"""Observe native SSE completion and usage without changing bytes sent to Pi."""
from __future__ import annotations

import json
import re
from copy import deepcopy

from ..models.client import ModelProtocolError, ModelSettings
from ..models.protocols import parse_reply


class NativeStream:
    def __init__(self, settings: ModelSettings, max_event_bytes: int):
        self.settings = settings
        self.max_event_bytes = max_event_bytes
        self.buffer = bytearray()
        self.response = None
        self.blocks = {}
        self.arguments = {}
        self.done = False

    def feed(self, chunk: bytes) -> None:
        self.buffer.extend(chunk)
        while match := re.search(rb'\r?\n\r?\n', self.buffer):
            frame = bytes(self.buffer[:match.start()])
            del self.buffer[:match.end()]
            if len(frame) > self.max_event_bytes:
                raise ModelProtocolError('native SSE event exceeds max_response_bytes')
            data = b'\n'.join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b'data:'))
            if not data:
                continue
            try:
                event = json.loads(data)
                self._event(event)
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise ModelProtocolError(f'Invalid {self.settings.api} SSE: {error}; frame={data[:500]!r}') from error
        if len(self.buffer) > self.max_event_bytes:
            raise ModelProtocolError('native SSE event exceeds max_response_bytes')

    def _event(self, event: dict) -> None:
        api = self.settings.api
        if 'error' in event or event.get('type') == 'error':
            raise ModelProtocolError(f'Native model stream error: {event}', response=event)
        if api == 'openai-responses':
            if event['type'] in {'response.completed', 'response.incomplete', 'response.failed'}:
                self.response = event['response']
                parse_reply(self.settings, self.response)
                self.done = True
        elif api == 'anthropic':
            kind = event['type']
            if kind == 'message_start':
                self.response = deepcopy(event['message'])
            elif kind == 'content_block_start':
                self.blocks[event['index']] = deepcopy(event['content_block'])
            elif kind == 'content_block_delta':
                index, delta = event['index'], event['delta']
                block = self.blocks[index]
                if delta['type'] == 'input_json_delta':
                    self.arguments[index] = self.arguments.get(index, '') + delta['partial_json']
                else:
                    field = {'text_delta': 'text', 'thinking_delta': 'thinking', 'signature_delta': 'signature'}[delta['type']]
                    block[field] = block.get(field, '') + delta[field]
            elif kind == 'content_block_stop':
                index = event['index']
                if index in self.arguments:
                    self.blocks[index]['input'] = json.loads(self.arguments[index])
            elif kind == 'message_delta':
                self.response.update(event['delta'])
                self.response.setdefault('usage', {}).update(event.get('usage', {}))
            elif kind == 'message_stop':
                self.response['content'] = [self.blocks[index] for index in sorted(self.blocks)]
                parse_reply(self.settings, self.response)
                self.done = True
        else:
            if self.response is None:
                self.response = {'candidates': [{'content': {'role': 'model', 'parts': []}}]}
            if event.get('candidates'):
                candidate = event['candidates'][0]
                saved = self.response['candidates'][0]
                saved['content']['parts'].extend(candidate.get('content', {}).get('parts', []))
                if 'finishReason' in candidate:
                    saved['finishReason'] = candidate['finishReason']
            if 'usageMetadata' in event:
                self.response.setdefault('usageMetadata', {}).update(event['usageMetadata'])

    def finish(self):
        if self.buffer.strip():
            raise ModelProtocolError(f'Native SSE ended in a partial event: {bytes(self.buffer[:500])!r}')
        if self.settings.api != 'gemini' and not self.done:
            raise ModelProtocolError('Native SSE ended before its completion event', response=self.response)
        return parse_reply(self.settings, self.response)
