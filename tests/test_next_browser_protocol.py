"""Native browser IPC envelope and host-bound arguments; synthetic wire samples."""
import asyncio
import json
from pathlib import Path
import tempfile

import pytest
from pydantic import ValidationError

from len_bot.next.browser.client import AccountBrowser, AccountBrowserSettings, BrowserAction, BrowserRPCError


def test_browser_native_frames():
    async def exercise(root):
        response_mode = 'ok'
        async def serve(reader, writer):
            request = json.loads(await reader.readline())
            if response_mode == 'error':
                response = {'id':request['id'],'error':{'code':'not_found','message':'fixture gone'}}
            elif response_mode == 'mismatch':
                response = {'id':'another-call','result':{}}
            elif request['method'] == 'session.start':
                response = {'id':request['id'],'result':{'session_id':'fixture-session','browser_instance_id':'fixture-browser'}}
            elif request['method'] == 'session.stop':
                response = {'id':request['id'],'result':{'stopped':['fixture-session']}}
            else:
                response = {'id':request['id'],'result':{'session_id':request['params']['session_id'],'text':'fixture page'}}
            writer.write((json.dumps(response)+'\n').encode());await writer.drain();writer.close();await writer.wait_closed()
        settings = AccountBrowserSettings(socket=root/'ipc.sock', browser_instance_id='fixture-browser', binary=root/'bsk', home=root)
        server = await asyncio.start_unix_server(serve, path=settings.socket)
        async with server:
            client = AccountBrowser(settings)
            session = await client.start()
            assert session == 'fixture-session'
            assert (await client.execute(session, BrowserAction(method='observe')))['session_id'] == session
            assert (await client.stop(session))['stopped'] == [session]
            response_mode = 'error'
            with pytest.raises(BrowserRPCError, match='fixture gone'):
                await client.execute(session, BrowserAction(method='observe'))
            response_mode = 'mismatch'
            with pytest.raises(ValueError, match='native RPC id'):
                await client.start()
    with tempfile.TemporaryDirectory(prefix='lb-bsk-', dir='/tmp') as directory:
        asyncio.run(exercise(Path(directory)))


@pytest.mark.parametrize('body', [
    {'method':'observe','params':{'session_id':'another-task'}},
    {'method':'navigate','params':{'browser_instance_id':'daily-browser'}},
    {'method':'upload','params':{}},
    {'method':'upload','params':{'ref':'@e1'},'files':[{'scope':'runtime','path':'control/task-api.json'}]},
    {'method':'upload','params':{'ref':'@e1'},'files':[{'scope':'inputs','path':'../another-task'}]},
    {'method':'download','params':{'ref':'@e1'},'save':True},
])
def test_browser_bound_arguments(body):
    with pytest.raises(ValidationError):
        BrowserAction.model_validate(body)


def test_human_wait_uses_native_deadline_not_normal_rpc_deadline():
    async def exercise(root):
        seen = []
        async def serve(reader, writer):
            request = json.loads(await reader.readline()); seen.append(request)
            await asyncio.sleep(.12)
            writer.write((json.dumps({'id': request['id'], 'result': {'outcome': 'completed'}})+'\n').encode())
            await writer.drain(); writer.close(); await writer.wait_closed()
        settings = AccountBrowserSettings(socket=root/'ipc.sock', binary=root/'bsk', home=root, timeout_seconds=.04)
        server = await asyncio.start_unix_server(serve, path=settings.socket)
        async with server:
            reply = await AccountBrowser(settings).execute('fixture-session', BrowserAction(
                method='request_help', params={'prompt': '合成人工等待', 'timeout_ms': 1000}))
            assert reply['outcome'] == 'completed'
            assert [request['method'] for request in seen] == ['tool.request_help']
            assert seen[0]['params']['session_id'] == 'fixture-session'
    with tempfile.TemporaryDirectory(prefix='lb-bsk-wait-', dir='/tmp') as directory:
        asyncio.run(exercise(Path(directory)))


@pytest.mark.parametrize('params', [{'ref': 'e1', 'value': 'red'}, {'values': 'red'}, {'values': [1]}])
def test_select_rejects_non_native_values(params):
    with pytest.raises(ValidationError, match='values'):
        BrowserAction(method='select', params=params)
