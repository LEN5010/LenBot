"""Pi 0.87.1 JSONL boundary: a recorded ACK and the installed RPC event contract.

The ACK is from the local worker failure on 2026-10-01, with its correlation
ID normalized. Event shapes are from that fixed SDK's docs/json.md. The child
only replays wire bytes; it does not run Pi, a model, tools, or task scheduling.
"""

import asyncio
import json
import os
import sys

from len_bot.next.work.pi_rpc import PiRpc


def test_prompt_ack_without_data_and_native_completion_frames(tmp_path):
    frames = [
        {"id": "lenbot-1", "type": "response", "command": "prompt", "success": True},
        {"type": "agent_start"},
        {"type": "message_update", "usage": {
            "input": 100, "output": 1, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 101,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        }, "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "Hello "}},
        {"type": "agent_end", "messages": [], "willRetry": False},
        {"type": "agent_settled"},
    ]
    wire = b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)
    replay = (
        "import sys\n"
        "sys.stdin.buffer.readline()\n"
        f"sys.stdout.buffer.write({wire!r})\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stdin.buffer.read()\n"
    )

    async def exercise():
        pi = await PiRpc.spawn([sys.executable, "-u", "-c", replay], cwd=tmp_path,
                               env=os.environ, stderr_path=tmp_path / "stderr.log")
        try:
            response = await asyncio.wait_for(pi.command("prompt", message="脱敏任务"), 5)
            assert response.body == frames[0]
            assert "data" not in response.body
            events = [await asyncio.wait_for(pi.next_event(), 5) for _ in frames[1:]]
            assert [event.body for event in events] == frames[1:]
            assert [event.raw for event in events] == wire.splitlines()[1:]
        finally:
            await pi.close()

    asyncio.run(exercise())


def test_fifo_stderr_fails_without_blocking_the_subprocess_protocol(tmp_path):
    import subprocess
    import pytest
    if os.name != 'posix':
        pytest.skip('Named-pipe task files require POSIX')
    fifo = tmp_path / 'stderr.log'
    os.mkfifo(fifo)
    program = (
        'import asyncio, os, sys\nfrom pathlib import Path\n'
        'from len_bot.next.work.pi_rpc import PiRpc\n'
        'asyncio.run(PiRpc.spawn([], cwd=Path.cwd(), env=os.environ, stderr_path=Path(sys.argv[1])))\n'
    )
    result = subprocess.run([sys.executable, '-c', program, str(fifo)], capture_output=True, timeout=3)
    assert result.returncode != 0 and b'OSError' in result.stderr
