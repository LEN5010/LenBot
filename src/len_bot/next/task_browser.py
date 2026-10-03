"""Task-bound account-browser sessions and human handoff through the native client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json

from .account_browser import AccountBrowser, BrowserAction
from .config import HostConfig
from .tasks_store import TERMINAL, Task, TaskStore


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
        action = BrowserAction.model_validate_json(raw)
        if action.method == 'screenshot' and self.config.worker.input_support != 'text-image':
            raise ValueError('当前工作模型未配置图像输入，不能向它提供浏览器截图')
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
        if action.method == 'screenshot':
            if not isinstance(result.get('image_base64'), str):
                raise ValueError(f'Browser screenshot lacks image_base64: {result!r}')
            return {'content': json.dumps({k:v for k,v in result.items() if k != 'image_base64'}, ensure_ascii=False),
                    'image': {'data': result['image_base64'], 'mimeType': 'image/png'}}
        return {'content': json.dumps(result, ensure_ascii=False)}

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
