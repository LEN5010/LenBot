"""LF-delimited native input for the single-scene lab and simulated host."""

import asyncio
from collections.abc import AsyncIterator
import json
import os
import stat
import sys


async def input_lines() -> AsyncIterator[str]:
    if stat.S_ISREG(os.fstat(sys.stdin.fileno()).st_mode):
        for line in sys.stdin.buffer:
            yield decode_line(line)
            await asyncio.sleep(0)
        return
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        pending = b''
        while chunk := await reader.read(65536):
            lines = (pending + chunk).split(b'\n')
            pending = lines.pop()
            for line in lines:
                yield decode_line(line)
                await asyncio.sleep(0)
        if pending:
            yield decode_line(pending)
    finally:
        transport.close()


def decode_line(raw: bytes) -> str:
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as error:
        fragment = raw[max(0, error.start - 50):error.end + 50]
        raise ValueError(f'Invalid stdin UTF-8: {error}; raw={fragment!r}') from error


def _reject_constant(value: str) -> None:
    raise ValueError(f'non-standard JSON constant {value}')


def parse_input(line: str, bot_qq: str) -> dict:
    try:
        value = json.loads(line, parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise ValueError('stdin record must be a native OneBot object')
        post_type = value.get('post_type')
        if not isinstance(post_type, str) or post_type not in {'message', 'notice', 'request', 'meta_event'}:
            raise ValueError('stdin record requires a native post_type')
        identity = value.get('self_id')
        if type(identity) not in (int, str) or str(identity) != bot_qq:
            raise ValueError(f'stdin self_id must match configured Bot QQ {bot_qq}')
        return value
    except ValueError as error:
        raise ValueError(f'Invalid stdin record: {error}; raw={line[:500]!r}') from error
