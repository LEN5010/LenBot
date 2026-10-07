"""Start a throwaway LenBot instance with made-up groups for the site screenshots.

A fake OneBot answers account and group queries and replays a few invented group messages; delivery is
simulated and no model is called. Nothing here touches a real QQ account or a real instance.

    uv run --no-sync python website/scripts/demo_instance.py /tmp/lenbot-demo
"""

import argparse
import asyncio
import itertools
import json
from pathlib import Path
import shutil
import socket
import sys
import time
import urllib.request

import websockets

ROOT = Path(__file__).resolve().parents[2]
BOT, OWNER = 90001, 70001
GROUPS = {80001: '周末桌游局', 80002: '摄影爱好者', 80003: '读书会'}
MEMBERS = {70001: '阿砚', 70002: '小满', 70003: '老周', 70004: '橘子', 70005: '清和'}
CHATS = [
    (80001, 70002, '这周六还是老地方吗'),
    (80001, 70003, '我带新到的那盒卡坦岛扩展'),
    (80001, 70004, '上次谁把骰子带走了，自首吧'),
    (80001, 70002, '六点开局，晚到的负责点外卖'),
    (80002, 70005, '今天傍晚的火烧云拍到了，可惜手抖'),
    (80002, 70001, '下次带个小三脚架，轻一点的就行'),
    (80002, 70003, '周日去植物园扫街有人吗'),
    (80003, 70004, '这个月读到第三章了，节奏有点慢'),
    (80003, 70005, '第四章开始就好看了，坚持一下'),
]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


async def onebot(port: int, ready: asyncio.Event) -> None:
    """Answer the queries LenBot makes and push the invented messages once it is connected."""
    counter = itertools.count(1000)

    def answer(action: str, params: dict):
        if action == 'get_login_info':
            return {'user_id': BOT, 'nickname': '小然'}
        if action in ('get_group_info', 'get_group_detail_info'):
            return {'group_id': params['group_id'], 'group_name': GROUPS.get(params['group_id'], '群'), 'member_count': 42}
        if action == 'get_group_list':
            return [{'group_id': group, 'group_name': name, 'member_count': 42} for group, name in GROUPS.items()]
        if action in ('get_group_member_info', 'get_stranger_info'):
            user = params['user_id']
            return {'user_id': user, 'nickname': MEMBERS.get(user, '群友'), 'card': '', 'role': 'member'}
        if action.startswith('send_'):
            return {'message_id': next(counter)}
        return {}

    async def handle(connection):
        async def replay():
            await asyncio.sleep(3)
            for group, user, text in CHATS:
                await connection.send(json.dumps({
                    'post_type': 'message', 'message_type': 'group', 'sub_type': 'normal', 'time': int(time.time()),
                    'self_id': BOT, 'group_id': group, 'user_id': user, 'message_id': next(counter),
                    'message': [{'type': 'text', 'data': {'text': text}}], 'raw_message': text, 'font': 0,
                    'sender': {'user_id': user, 'nickname': MEMBERS[user], 'card': '', 'role': 'member'}}))
                await asyncio.sleep(0.4)
            ready.set()
        task = asyncio.create_task(replay())
        try:
            async for raw in connection:
                request = json.loads(raw)
                await connection.send(json.dumps({'status': 'ok', 'retcode': 0, 'echo': request.get('echo'),
                                                  'data': answer(request['action'], request.get('params') or {})}))
        finally:
            task.cancel()

    async with websockets.serve(handle, '127.0.0.1', port):
        await asyncio.Future()


def post(url: str, body: dict, headers: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', type=Path, help='New directory for the throwaway instance')
    parser.add_argument('--panel-port', type=int, default=18088)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True)
    onebot_port = free_port()
    ready = asyncio.Event()
    server = asyncio.create_task(onebot(onebot_port, ready))
    command = [sys.executable, '-c', 'from len_bot import main; main()']
    process = None
    draining = None
    waiting = None
    ready_task = None
    async def drain(child):
        while line := await child.stdout.readline():
            print(line.decode('utf-8', 'replace'), end='', flush=True)
    try:
        process = await asyncio.create_subprocess_exec(*command, cwd=root, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.STDOUT)
        async with asyncio.timeout(60):
            while True:
                line = (await process.stdout.readline()).decode('utf-8', 'replace')
                if '#token=' in line:
                    break
                if not line:
                    raise RuntimeError(f'len-bot exited before setup with {await process.wait()}')
        draining = asyncio.create_task(drain(process))
        setup = line[line.index('http://'):].split()[0]
        base, token = setup.split('/#token=')
        await asyncio.to_thread(post, base + '/api/setup', {
            'bot_id': f'onebot:{BOT}', 'owners': [f'onebot:{OWNER}'], 'timezone': 'Asia/Shanghai', 'delivery': 'simulated',
            'onebot': {'mode': 'forward_ws', 'ws_url': f'ws://127.0.0.1:{onebot_port}', 'access_token': 'demo'},
            'provider': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1', 'api_key': 'demo'},
            'mind': {'provider': 'primary', 'model': 'demo-chat', 'context_window_tokens': 128000},
            'compaction': {'input_tokens': 96000}, 'scene': 'onebot:group:80001', 'persona_id': 'companion',
            'persona_name': '小然', 'brief': '演示角色', 'voice_text': '简短', 'boundaries': '',
            'panel_port': args.panel_port, 'username': 'demo', 'password': 'demo-password'}, {'X-Setup-Token': token})
        process.terminate()
        async with asyncio.timeout(60):
            await process.wait()
            await draining
        draining = None
        shutil.rmtree(root / 'personas/companion')
        shutil.copytree(ROOT / 'examples/personas/companion', root / 'personas/companion')
        config = json.loads((root / 'lenbot.config.json').read_text())
        first = config['scenes']['onebot:group:80001']
        for group in GROUPS:
            config['scenes'][f'onebot:group:{group}'] = {**first, 'attention': {**first.get('attention', {}), 'only_direct': True}}
        (root / 'lenbot.config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        process = await asyncio.create_subprocess_exec(*command, cwd=root)
        waiting = asyncio.create_task(process.wait())
        ready_task = asyncio.create_task(ready.wait())
        async with asyncio.timeout(60):
            done, _ = await asyncio.wait((waiting, ready_task), return_when=asyncio.FIRST_COMPLETED)
            if waiting in done:
                raise RuntimeError(f'len-bot exited before becoming ready: {waiting.result()}')
        print(f'演示面板：http://127.0.0.1:{args.panel_port}  用户 demo / demo-password；Ctrl-C 结束', flush=True)
        await waiting
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                async with asyncio.timeout(60):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (draining, waiting, ready_task, server):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (draining, waiting, ready_task, server) if task is not None),
                             return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
