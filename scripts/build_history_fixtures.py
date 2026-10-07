"""Rebuild tests/fixtures/history: small synthetic instances written by released-format code.

    uv run --no-sync python scripts/build_history_fixtures.py

Each fixture checks out the commit that last wrote that data format into a temporary
worktree and runs WRITER there, so the databases, JSON bodies and files come from the
old code itself rather than from today's schema guessed backwards. The tests migrate a
copy to the current formats and compare it with a freshly created instance.

Add a fixture when a format changes: the commit just before the change, named after the
formats it writes. Rebuilding an existing fixture should produce the same content.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'tests/fixtures/history'

# name -> commit. Names list config / business / memory jobs / local index formats.
FIXTURES = {
    'config0-business1-jobs5-index2': 'a572456',
    'config0-business2-jobs6-index2': 'c9ff238',
    'config2-business3-jobs6-index3': '02fb0ff',
}

WRITER = r'''
import dataclasses, inspect, itertools, json, sys, time, uuid
from pathlib import Path

# Same input, same bytes: the clock and generated IDs are fixed before the old code imports them.
clock = itertools.count(1_790_000_000)
now = lambda: float(next(clock))
time.time = now
identifiers = itertools.count(1)
uuid.uuid4 = lambda: uuid.UUID(int=next(identifiers))

from len_bot.next.config import load_host_config
from len_bot.next.memory.jobs import MemoryJobs
from len_bot.next.memory.local import LocalMemory, LocalMemorySettings
from len_bot.next.models.client import parse_token_usage
from len_bot.next.platform.messages import ChatMessage, Segment, Sender
from len_bot.next.storage.store import FORMAT_VERSION, Store
from len_bot.next.chat.schedule_store import ScheduleStore
from len_bot.next.work.store import TaskStore

root = Path(sys.argv[1])
SCENE, BOT, OWNER = 'onebot:group:80001', 'onebot:90001', 'onebot:70001'

role = root / 'role'
role.mkdir(parents=True)
(role / 'persona.yaml').write_text(json.dumps({
    'id': 'synthetic', 'name': '合成角色', 'brief': '历史样本。', 'behavior': '正常对话。',
    'self_reference': ['我'], 'aliases': [], 'tools': 'all', 'skills': [], 'styles': []}, ensure_ascii=False))
for name, body in [('voice.md', '简短。'), ('boundaries.md', '合成。'), ('examples.yaml', '[]\n')]:
    (role / name).write_text(body, encoding='utf-8')
(root / 'lenbot.config.json').write_text(json.dumps({
    'compaction': {'input_tokens': 2000}, 'mode': 'isolated-multi', 'bot_id': BOT, 'owners': [OWNER],
    'timezone': 'Asia/Shanghai', 'database': 'state.db',
    'onebot': {'mode': 'reverse_ws', 'listen_host': '127.0.0.1', 'listen_port': 0},
    'models': {'providers': {'sample': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'synthetic'}},
               'roles': {'mind': {'provider': 'sample', 'model': 'mind', 'context_window_tokens': 8192}}},
    'memory': {'backend': 'local', 'local': {'directory': 'memory'}},
    'scenes': {SCENE: {'persona': 'role'}},
}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
config = load_host_config(root)

usage = {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150}
store = Store(config.database, now=now)
for number, (sender, text) in enumerate([(OWNER, '明天提醒我交报告'), ('onebot:70002', '收到')], start=1):
    message = ChatMessage(id=f'm{number}', platform='onebot', bot_id=BOT, scene=SCENE, platform_message_id=str(number),
                          sender=Sender(uid=sender, nickname=f'群友{number}', card=None, role='member'),
                          time=now(), segments=[Segment(type='text', data={'text': text})], reply_to=None,
                          mentions_bot=number == 1, is_self=False, send_status='received')
    store.enqueue(message, {'message_id': number}, now())
turn = store.start_turn(SCENE)
call = store.start_call(turn, 'mind', {'model': 'mind', 'messages': [{'role': 'user', 'content': '明天提醒我交报告'}]})
money = 'cost' in inspect.signature(store.end_call).parameters
if not money:
    from len_bot.next.models.tokens import token_record
    tokens = token_record(parse_token_usage(usage))
store.end_call(call, {'message': {'role': 'assistant', 'content': '好'}}, usage,
               **({'cost': None} if money else {'tokens': tokens}))
store.end_turn(turn, 'done')
ScheduleStore(store).create_schedule(SCENE, due_at=now() + 86_400, timezone='Asia/Shanghai', note='交报告',
                                     target=OWNER if FORMAT_VERSION >= 3 else '70001', requester=OWNER, limit=10)
tasks = TaskStore(store)
task = tasks.create(SCENE, OWNER, '整理报告', '一页摘要', '群聊', '整理报告')
tasks.start(SCENE, task.id)
event = tasks.start_call(SCENE, task.id, {'model': 'worker'})
facts = {'status': 200, 'usage': usage, 'error': None, 'error_body': None}
if money:
    facts |= {'cost': None, 'token_usage': dataclasses.asdict(parse_token_usage(usage))}
else:
    facts |= {'tokens': tokens}
tasks.finish_call(event, facts)
tasks.add_file(SCENE, task.id, name='report.md', path='report.md', size=12, note='摘要')
tasks.finish(SCENE, task.id, 'done', summary='完成', error=None)
store.db.close()

with MemoryJobs(config.database.with_name(config.database.name + '.memory.sqlite3')) as jobs:
    jobs.initialize(SCENE, 0)
    job = jobs.create(SCENE, 'local', 1, 2)
    call = {'started': now(), 'ended': now(), 'usage': usage}
    job['details'] = {'calls': [call | ({'cost': None} if money else {'tokens': tokens})]}
    jobs.status(job, 'complete')

memory = LocalMemory(LocalMemorySettings(directory=root / 'memory'))
scene_dir = root / 'memory' / 'scenes'
memory._initialize()
note = scene_dir / 'onebot-group-80001' / 'people.md'
note.parent.mkdir(parents=True, exist_ok=True)
note.write_text('# 群友\n\n群友1 周五交报告。\n', encoding='utf-8')
if FORMAT_VERSION < 3:
    for name in ('.abstract.md', '.overview.md'):
        (note.parent / name).write_text('旧摘要\n', encoding='utf-8')
print(f'business {FORMAT_VERSION}')
'''


def build(name: str, commit: str) -> None:
    target = OUTPUT / name
    with tempfile.TemporaryDirectory() as temporary:
        worktree = Path(temporary) / 'source'
        subprocess.run(['git', 'worktree', 'add', '--detach', '--quiet', str(worktree), commit], cwd=ROOT, check=True)
        try:
            instance = Path(temporary) / 'instance'
            environment = {**os.environ, 'PYTHONPATH': str(worktree / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
            writer = Path(temporary) / 'writer.py'
            writer.write_text(WRITER, encoding='utf-8')
            subprocess.run([sys.executable, str(writer), str(instance)], env=environment, check=True)
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(instance, target)
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(worktree)], cwd=ROOT, check=True)
    (target / 'COMMIT').write_text(commit + '\n', encoding='utf-8')
    print(f'{name}: written by {commit}')


def main() -> None:
    for name, commit in FIXTURES.items():
        build(name, commit)


if __name__ == '__main__':
    main()
