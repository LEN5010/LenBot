"""Task-bound account-browser sessions and human handoff through the native client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
import json

from pydantic import ValidationError

from .client import AccountBrowser, BrowserAction
from .transfers import BrowserTransfers, FileTarget, ScreenshotTarget, UploadTarget
from ..config import HostConfig
from .files import BrowserOutput, browser_output_file, record_browser_output
from ..work.materials import finish_file_operation
from ..work.resources import ResourceFileRef, TaskResources
from ..work.store import TERMINAL, Task, TaskStore


class TaskBrowser:
    """Bind actual browser sessions to tasks without owning their execution state."""

    def __init__(self, config: HostConfig, records: TaskStore, client: AccountBrowser | None):
        self.config, self.records, self.client = config, records, client

    def require_owner(self, requester: str) -> None:
        if self.config.owner_qq is None or requester != self.config.owner_qq:
            raise PermissionError('账号浏览仅允许根配置的主人QQ')
        if self.client is None or self.client.settings.browser_instance_id is None:
            raise ValueError('账号浏览服务尚未配置或未明确绑定专用浏览器')

    async def start(self, item: Task) -> None:
        self.require_owner(item.requester)
        async with self.client.lock:
            if self.records.browser_in_use() or await self.client.sessions():
                raise ValueError('专用账号浏览器仍被占用，未启动本任务')
            self.records.browser_binding(item.scene, item.id, active=True, session=None)
            session_id = await self.client.start()
            self.records.browser_binding(item.scene, item.id, active=True, session=session_id)
        self.records.add_event(item.scene, item.id, 'browser_started', {'session_id': session_id,
            'socket': str(self.client.settings.socket), 'browser_instance_id': self.client.settings.browser_instance_id})

    async def stop(self, item: Task) -> None:
        if item.browser_session is None:
            raise RuntimeError('浏览器创建结果未知，保留配置占用；请从能力页检查实际会话并明确清理')
        result = await self.client.stop(item.browser_session)
        self.records.browser_binding(item.scene, item.id, active=False, session=None)
        self.records.add_event(item.scene, item.id, 'browser_stopped', result)

    async def execute(self, item: Task, raw: bytes, *,
                      pause_input: Callable[[dict], None],
                      resume_input: Callable[[], Awaitable[None]]) -> dict:
        if not item.account_browser:
            raise PermissionError('普通任务不能获得账号浏览能力，请另建主人授权任务')
        self.require_owner(item.requester)
        if not item.browser_active or item.browser_session is None:
            raise RuntimeError('此任务没有已确认的账号浏览会话')
        try:
            action = BrowserAction.model_validate_json(raw)
        except ValidationError as error:
            raise ValueError(f'Invalid browser action: {raw[:500]!r}; {error}') from error
        if action.method in {'upload', 'download', 'screenshot'}:
            return await self.file_action(item, action)
        if action.method == 'request_help':
            # The host's existing input wait limit governs human time, not active execution.
            params = {**action.params, 'timeout_ms': int(self.config.worker.input_timeout_seconds * 1000)}
            action = BrowserAction(method=action.method, params=params)
            question = {'method': 'request_help', 'title': params['prompt'], 'params': params}
            pause_input(question)
            self.records.add_event(item.scene, item.id, 'question', question,
                notice=f"[任务浏览器接手] #{item.id}；请在专用浏览器完成：{params['prompt']}")
            try:
                result = await self.client.execute(item.browser_session, action)
            except asyncio.CancelledError:
                raise
            except Exception:
                await resume_input()
                raise
            else:
                self.records.add_event(item.scene, item.id, 'answer', result)
                await resume_input()
        else:
            result = await self.client.execute(item.browser_session, action)
        return {'content': json.dumps(result, ensure_ascii=False)}

    async def file_action(self, item: Task, action: BrowserAction) -> dict:
        settings = self.config.worker
        images = settings.input_support == 'text-image'
        if action.method == 'screenshot' and not images and not action.save:
            raise ValueError('纯文本工作模型使用 screenshot 时设置 save=true，将图片保存到任务资源')
        target_type = {'upload': UploadTarget, 'download': FileTarget, 'screenshot': ScreenshotTarget}[action.method]
        try:
            target = target_type.model_validate(action.params)
        except ValidationError as error:
            raise ValueError(f'Invalid browser {action.method} params: {action.params!r}; {error}') from error
        transfers = BrowserTransfers(self.client)
        saved, attached = None, None
        try:
            async with self.client.lock:
                page = await transfers.page(item.browser_session, target.tab_id)
                target = target.model_copy(update={'tab_id': page.tab_id})
                source = {'page_url': page.url, 'page_title': page.title}
                if action.method == 'upload':
                    resources = TaskResources(settings, self.records)
                    with ExitStack() as streams:
                        files, references = [], []
                        for selection in action.files:
                            reference = ResourceFileRef(task_id=item.id, **selection.model_dump())
                            opened = resources.open(item.scene, reference)
                            streams.enter_context(opened.stream)
                            if opened.size > settings.max_file_bytes:
                                raise ValueError(f'Browser upload exceeds worker.max_file_bytes: {opened.name}; '
                                                 f'{opened.size} > {settings.max_file_bytes}')
                            files.append(opened)
                            references.append({'name': opened.name, 'size': opened.size, 'mime_type': opened.mime_type,
                                               'reference': reference.model_dump()})
                        async with transfers.upload(item.browser_session, target, files) as receipt:
                            attached = {'status': 'attached', 'files': references, **source, **receipt.model_dump()}
                            self.records.add_event(item.scene, item.id, 'browser_upload', attached)
                    return {'content': json.dumps(attached, ensure_ascii=False)}
                if action.method == 'download':
                    async with transfers.download(item.browser_session, target, settings.max_file_bytes) as receipt:
                        with browser_output_file(settings, item, 'download', receipt.suggested_filename) as (path, output):
                            await transfers.read(receipt, output)
                        saved = record_browser_output(settings, self.records, item,
                            BrowserOutput(path=path, kind='download', **source))
                    result = {'status': 'saved', 'file': saved,
                              'browser': receipt.model_dump(exclude={'transfer_id'})}
                    return {'content': json.dumps(result, ensure_ascii=False)}
                receipt, image = await transfers.screenshot(item.browser_session, target)
                result = {'status': 'captured', **source, **receipt.model_dump(exclude={'image_base64'})}
                if action.save:
                    if len(image) > settings.max_file_bytes:
                        raise ValueError(f'Browser screenshot exceeds worker.max_file_bytes: {len(image)} > {settings.max_file_bytes}')
                    with browser_output_file(settings, item, 'screenshot', 'page.png') as (path, output):
                        await finish_file_operation(output.write, image)
                    saved = record_browser_output(settings, self.records, item,
                        BrowserOutput(path=path, kind='screenshot', **source))
                    result.update(status='saved', file=saved)
                return {'content': json.dumps(result, ensure_ascii=False), **(
                    {'image': {'data': receipt.image_base64, 'mimeType': 'image/png'}} if images else {})}
        except BaseException as error:
            if saved is not None:
                error.add_note(f"Browser file already saved: {saved['path']}; reference={saved['reference']!r}")
            if attached is not None:
                error.add_note(f"Browser files already attached: {attached['file_names']!r}; tab_id={attached['tab_id']}")
            raise

    async def release(self, item: Task, *, session_id: str | None) -> dict:
        if item.status not in TERMINAL or not item.browser_active:
            raise ValueError('只清理已结束但仍占用浏览器的任务；活动任务应先取消')
        sessions = await self.client.sessions(bound=False)
        own = [row['session_id'] for row in sessions if row['browser_instance_id'] == self.client.settings.browser_instance_id]
        target = item.browser_session if session_id is None else session_id
        if item.browser_session is not None and target != item.browser_session:
            raise ValueError('只能关闭此任务已绑定的实际会话')
        if target is not None and any(row['session_id'] == target and row['browser_instance_id'] != self.client.settings.browser_instance_id for row in sessions):
            raise ValueError('原任务会话仍在其他浏览器绑定上；恢复原配置清理，不能按当前空配置释放')
        if target is not None and target in own:
            await self.client.stop(target)
            own.remove(target)
        elif target is not None and target != item.browser_session:
            raise ValueError('所选会话不属于当前专用浏览器')
        if own:
            raise ValueError(f'专用浏览器仍有会话：{own!r}；请明确选择实际会话清理')
        self.records.browser_binding(item.scene, item.id, active=False, session=None)
        self.records.add_event(item.scene, item.id, 'browser_released', {'session_id': target, 'confirmed_empty': True})
        return {'released': True}
