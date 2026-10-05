"""Request history policy for a verified transport, separate from response storage."""

from typing import Literal


HistoryPolicy = Literal['native', 'omit-reasoning']


def project_messages(messages: list[dict], policy: HistoryPolicy) -> list[dict]:
    if policy == 'native':
        return messages
    projected = []
    for message in messages:
        if message['role'] != 'assistant':
            projected.append(message)
            continue
        # This route reconstructs its own signed continuation. Opaque extensions
        # and native call IDs are retained; readable unsigned analysis is optional.
        item = {key: value for key, value in message.items() if key != 'reasoning_content'}
        if (set(item) <= {'role', 'content', 'tool_calls'} and not item['content']
                and not item.get('tool_calls')):
            continue
        projected.append(item)
    return projected


def project_old_results(entries: list[tuple[int, dict]], keep_recent_tokens: int) -> list[tuple[int, dict]]:
    """Keep recent results whole; older saved web pages remain readable by document ID."""
    import json
    from math import ceil
    from pathlib import Path
    from ..storage.codec import encode

    template = (Path(__file__).resolve().parents[2] / 'prompts' / 'next_web_read.md').read_text()
    prefix, suffix = template.split('$result')
    recent, cutoff = 0, 0
    for seq, message in reversed(entries):
        recent += ceil(len(encode(message).encode('utf-8')) / 3)
        if recent > keep_recent_tokens:
            cutoff = seq
            break
    calls = {}
    projected = []
    for seq, message in entries:
        if message['role'] == 'assistant':
            for call in message.get('tool_calls') or []:
                calls[call['id']] = call['function']['name']
        if (seq <= cutoff and message['role'] == 'tool'
                and calls[message['tool_call_id']] == 'web_read'
                and message['content'].startswith(prefix) and message['content'].endswith(suffix)):
            page = json.loads(message['content'][len(prefix):len(message['content']) - len(suffix)])
            metadata = {key: value for key, value in page.items() if key != 'text'}
            message = {**message, 'content': encode(metadata) +
                       '\n这是较早读过的网页位置；正文仍保存在该 document，可用 web_read(document, offset) 重读。'}
        projected.append((seq, message))
    return projected
