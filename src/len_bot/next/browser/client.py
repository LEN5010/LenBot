"""One configured remote browser profile, via the daemon's native local IPC."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..work.resources import ResourceLocation


class AccountBrowserSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    socket: Path
    browser_instance_id: str | None = Field(default=None, min_length=1)
    binary: Path
    home: Path
    timeout_seconds: float = Field(default=65, gt=0, le=600)
    max_response_bytes: int = Field(default=16000000, ge=1024)

    @field_validator('socket', 'binary', 'home')
    @classmethod
    def absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError('must be an explicit absolute path')
        return value


Method = Literal['navigate', 'navigate_back', 'navigate_forward', 'reload', 'observe', 'snapshot',
                 'click', 'fill', 'press', 'select', 'wheel', 'scroll_to', 'hover', 'screenshot',
                 'tab_list', 'tab_create', 'tab_select', 'tab_close', 'tab_borrow', 'tab_return',
                 'request_help', 'get_html', 'upload', 'download']


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scope: Literal['inputs', 'workspace']
    path: str = Field(min_length=1)

    @field_validator('path')
    @classmethod
    def relative_path(cls, value: str) -> str:
        return ResourceLocation.relative_path(value)


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    method: Method
    params: dict = Field(default_factory=dict)
    files: list[BrowserInput] = Field(default_factory=list, max_length=20,
        description='upload 使用的当前任务文件；scope 为 inputs 或 workspace，path 相对于该目录。')
    save: bool = Field(default=False, description='screenshot 是否同时保存到任务 out/browser/；纯文本模型可用此方式保存截图。')

    @field_validator('params')
    @classmethod
    def bound_session(cls, value: dict) -> dict:
        if {'session_id', 'browser_instance_id'} & value.keys():
            raise ValueError('session and browser are bound by the host, not tool arguments')
        return value


    @model_validator(mode='after')
    def native_arguments(self):
        if (self.method == 'upload') != bool(self.files):
            raise ValueError('upload requires files; other methods do not accept files')
        if self.save and self.method != 'screenshot':
            raise ValueError('save only applies to screenshot; download always saves to the task')
        if self.method == 'select':
            values = self.params.get('values')
            if 'value' in self.params or not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f'select requires values: string[]; raw={self.params!r}')
        if self.method == 'request_help':
            prompt = self.params.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f'request_help requires nonblank prompt; raw={self.params!r}')
            if 'timeout_ms' in self.params:
                timeout = self.params['timeout_ms']
                if type(timeout) is not int or not 1 <= timeout <= 4294967295:
                    raise ValueError(f'request_help timeout_ms must be positive u32; raw={self.params!r}')
        return self


BROWSER_TOOL = {'name': 'account_browser',
    'description': '在此主人授权的独立账号任务中操作专用浏览器。方法与参数使用原生浏览器协议；'
                   '先observe取得实际ref再操作。session和browser由宿主绑定。'
                   'upload的files选择当前任务inputs/workspace相对路径，params用ref或selector、可选tab_id/timeout_ms/mode(input或drop)。'
                   'download的params用ref或selector、可选tab_id/timeout_ms，完成后返回任务文件。'
                   'screenshot可设save=true保存任务截图。附加文件、网页提交、任务文件登记和平台发送分别进行。'
                   '登录/验证码使用request_help；不可撤回操作先confirm_action。',
    'parameters': BrowserAction.model_json_schema()}


class BrowserRPCError(RuntimeError):
    pass


class AccountBrowser:
    def __init__(self, settings: AccountBrowserSettings):
        self.settings = settings
        self.lock = asyncio.Lock()

    async def rpc(self, method: str, params: dict, *, response_timeout_seconds: float | None = None) -> dict:
        call_id = str(uuid4())
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(
            self.settings.socket, limit=self.settings.max_response_bytes), self.settings.timeout_seconds)
        try:
            async with asyncio.timeout(self.settings.timeout_seconds if response_timeout_seconds is None
                                       else response_timeout_seconds):
                writer.write((json.dumps({'id': call_id, 'method': method, 'params': params}) + '\n').encode())
                await writer.drain()
                raw = await reader.readline()
                try:
                    result = json.loads(raw)
                    if not isinstance(result, dict) or result.get('id') != call_id:
                        raise ValueError('response must match the native RPC id')
                    if set(result) not in ({'id','result'}, {'id','error'}):
                        raise ValueError('response needs exactly result or error')
                    if not isinstance(result.get('result', result.get('error')), dict):
                        raise ValueError('result/error must be an object')
                except (ValueError, UnicodeDecodeError) as error:
                    raise ValueError(f'Browser IPC parse failed: {error}; raw={raw[:500]!r}') from error
                if 'error' in result:
                    raise BrowserRPCError(json.dumps(result['error'], ensure_ascii=False))
                return result['result']
        except (TimeoutError, asyncio.CancelledError) as error:
            if method != 'cancel':
                try:
                    await self.rpc('cancel', {'rpc_id': call_id})
                except Exception as cancel_error:
                    error.add_note(f'Browser cancellation not confirmed: {cancel_error}')
            raise
        finally:
            writer.close()
            await writer.wait_closed()

    async def sessions(self, *, bound: bool = True) -> list[dict]:
        result = await self.rpc('session.list', {})
        sessions = result.get('sessions')
        if not isinstance(sessions, list) or any(not isinstance(row, dict)
                or not isinstance(row.get('session_id'), str)
                or not isinstance(row.get('browser_instance_id'), str) for row in sessions):
            raise ValueError(f'Invalid native session list: {result!r}')
        return ([row for row in sessions if row['browser_instance_id'] == self.settings.browser_instance_id]
                if bound else sessions)

    async def start(self) -> str:
        if self.settings.browser_instance_id is None:
            raise ValueError('专用浏览器尚未绑定；先配对、读取实际instance_id并明确保存')
        result = await self.rpc('session.start', {'browser_instance_id': self.settings.browser_instance_id, 'focused': False})
        if (not isinstance(result.get('session_id'), str) or not result['session_id']
                or result.get('browser_instance_id') != self.settings.browser_instance_id):
            raise ValueError(f'Browser session.start returned invalid binding: {result!r}')
        return result['session_id']

    async def stop(self, session: str) -> dict:
        result = await self.rpc('session.stop', {'session_id': session, 'all': False})
        if (not isinstance(result.get('stopped'), list) or session not in result['stopped']
                or result.get('failed') or result.get('return_failures')):
            raise BrowserRPCError(f'Browser stop not confirmed: {result!r}')
        return result

    async def execute(self, session: str, action: BrowserAction) -> dict:
        async with self.lock:
            timeout = ((action.params.get('timeout_ms', 300000) / 1000 + 15)
                       if action.method == 'request_help' else None)
            result = await self.rpc('tool.' + action.method, {**action.params, 'session_id': session},
                                    response_timeout_seconds=timeout)
            if action.method == 'screenshot':
                try:
                    data = base64.b64decode(result['image_base64'], validate=True)
                    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                        raise ValueError('screenshot is not PNG')
                except (KeyError, TypeError, ValueError, binascii.Error) as error:
                    raise ValueError(f'Browser screenshot parse failed: {error}; raw={repr(result)[:500]}') from error
            return result

    async def management(self, command: Literal['pair','devices','revoke'], device: str | None = None) -> dict:
        raw_info = await asyncio.to_thread((self.settings.home / 'daemon.json').read_text)
        try:
            info = json.loads(raw_info)
            if not isinstance(info, dict) or info.get('sock_path') != str(self.settings.socket):
                raise ValueError('configured home and socket must identify the same daemon')
        except ValueError as error:
            raise ValueError(f'Browser daemon metadata invalid: {error}; raw={raw_info[:500]!r}') from error
        args = [str(self.settings.binary), '--json', 'daemon', command]
        if command == 'revoke':
            if device is None or not device or device.startswith('-'):
                raise ValueError('revoke requires an actual device ID')
            args.append(device)
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={'BSK_HOME': str(self.settings.home), 'BSK_AUTO_START':'0',
                                               'HOME':str(self.settings.home), 'PATH':'/usr/bin:/bin'})
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.settings.timeout_seconds)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise BrowserRPCError(f'browser {command} exit {process.returncode}: {stderr.decode("utf-8")}')
        try:
            text = stdout.decode('utf-8').strip()
            if command == 'pair':
                link = urlsplit(text)
                if link.scheme not in {'ws', 'wss'} or not link.netloc or not link.fragment:
                    raise ValueError('pair must return its native one-use connection link')
                return {'pairing_link': text}
            if command == 'revoke':
                if text != 'Authorization revoked':
                    raise ValueError('revoke did not return its native confirmation')
                return {'revoked': device}
            result = json.loads(text)
            if not isinstance(result, list):
                raise ValueError('devices must return a list')
            return {'devices': result}
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError(f'browser {command} invalid JSON: {error}; raw={stdout[:500]!r}') from error
