"""Scene-owned task lifecycle, permissions, scheduling and completion notices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from string import Template
import traceback
from typing import Literal, TYPE_CHECKING
from zoneinfo import ZoneInfo

from .config import HostConfig
from .audio import AudioService
from .account_browser import AccountBrowser
from .identity import roles_for
from .egress_usage import EgressUsage
from .model_slots import ModelSlots
from .memory import MemoryService
from .pricing import cost_summary
from .sandbox import DockerSandbox, DockerSettings
from .skills import Skill
from .store import Store
from .task_execution import TaskExecution
from .task_files import TaskFiles, file_info
from .task_browser import TaskBrowser
from .tasks_store import TERMINAL, Task, TaskStore
from .worker_model import Limits
from .task_materials import MaterialName, finish_file_operation
from .task_inputs import copy_inputs, create_stage, publish_inputs, remove_stage
from .operations import credentials, diagnostic_value, redact, redact_record

if TYPE_CHECKING:
    from .mcp_host import MCPHost


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class WorkTasks:
    def __init__(self, config: HostConfig, store: Store, slots: ModelSlots | None,
                 on_update: Callable[[str], None], *, skills: dict[str, tuple[Skill, ...]],
                 memory: MemoryService | None, data_tools: dict[str, list[dict]],
                 skill_permissions: dict[str, Literal["all"] | list[str]],
                 tool_permissions: dict[str, Literal["all"] | list[str]]):
        self.config, self.store, self.slots = config, store, slots
        self.settings = config.worker
        self.browser = None if config.account_browser is None else AccountBrowser(config.account_browser)
        self.records = TaskStore(store)
        self.on_update = on_update
        self.skills = skills
        self.skill_permissions = skill_permissions
        self.memory = memory
        self.audio: AudioService | None = None
        self.data_tools = data_tools
        self.tool_permissions = tool_permissions
        self.mcp: MCPHost | None = None
        self.egress = EgressUsage(config, self.records, on_update)
        settings = self.settings
        self.sandbox = DockerSandbox(DockerSettings(
            docker_binary=settings.docker_binary, docker_host=settings.docker_host,
            image=settings.image, workspace_root=settings.workspace_root,
            runtime_root=settings.runtime_root, uid=settings.uid, gid=settings.gid,
            cpus=settings.cpus, memory=settings.memory, pids_limit=settings.pids_limit,
            tmpfs_size=settings.tmpfs_size,
            command_timeout_seconds=settings.command_timeout_seconds,
        ))
        self.files = TaskFiles(settings, self.records, self.sandbox, self._notify)
        self.task_browser = TaskBrowser(config, self.records, self.browser)
        self.running: dict[int, TaskExecution] = {}
        self.file_changes = asyncio.Lock()
        self.live_listeners: dict[int, set[asyncio.Event]] = {}
        self.changed = asyncio.Event()
        self.accepting = False
        self._pump: asyncio.Task | None = None
        self.error: str | None = None
        self._failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    def _notify(self, scene: str) -> None:
        self.changed.set()
        self.on_update(scene)

    def _roles(self, scene: str, requester: str) -> set[str]:
        if requester == self.config.bot_qq:
            raise PermissionError("委托与管理任务须使用实际人类 QQ，不能用 Bot 账号")
        settings = self.config.scenes[scene].tasks
        identities = self.config.scene_config(scene).permissions
        return roles_for(requester, owner=self.config.owner_qq, scoped_owner=settings.owner,
                         admins=[*settings.admins, *identities.admins], whitelist=[*settings.whitelist, *identities.whitelist],
                         group_role=self.store.latest_sender_role(scene, requester) if scene.startswith('group:') else None)

    def _can_delegate(self, scene: str, requester: str) -> None:
        if not self.accepting:
            raise RuntimeError(f"任务执行器未接受新任务：{self.error or '宿主正在启动或停止'}")
        if requester in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError(f'QQ {requester} 在当前场景黑名单中，不能创建或继续任务')
        settings = self.config.scenes[scene].tasks
        if not settings.enabled:
            raise PermissionError("当前场景未开放任务执行")
        if not self._roles(scene, requester).intersection(settings.delegate_roles):
            raise PermissionError(f"QQ {requester} 没有当前场景的委托任务权限")

    def _can_manage(self, item: Task, requester: str) -> None:
        if requester != item.requester and requester in self.config.scene_config(item.scene).permissions.blacklist:
            raise PermissionError('黑名单账号只能取消本人的任务，不能管理他人任务')
        if item.account_browser:
            self.task_browser.require_owner(requester)
        roles = self._roles(item.scene, requester)
        if requester != item.requester and not roles.intersection(
                self.config.scenes[item.scene].tasks.manage_roles):
            raise PermissionError("只能管理自己的任务，或由有任务管理权限的账号操作")

    def active_timeout(self, item: Task) -> float:
        current = self.running.get(item.id)
        if current is not None and current.active_limit > 0:
            return current.active_limit
        requester = self.records.execution_requester(item)
        allowed = self._roles(item.scene, requester).intersection(self.config.scenes[item.scene].tasks.long_running_roles)
        return self.settings.active_timeout_seconds if allowed else min(self.settings.active_timeout_seconds, 1800)

    def status(self, scene: str, id: int) -> dict:
        item = self.records.get(scene, id)
        costs = self.records.call_costs(scene, id)
        return {**asdict(item), "files": [file_info(file, self.records) for file in self.records.list_files(scene, id)],
                "workspace_discard_requested": self.records.workspace_discarded(scene, id),
                "model_calls": len(costs), "cost": cost_summary(costs), "active_timeout_seconds": self.active_timeout(item),
                "network": self.egress.status(scene, id),
                "notice": "done 只表示执行正常结束；文件登记不表示已上传 QQ。出网配置不等于目标连通。"}

    def live_snapshot(self, scene: str, id: int) -> dict:
        item = self.records.get(scene, id)
        current = self.running.get(id)
        value = {"task_id": item.id, "scene": item.scene, "status": item.status,
                 "preview": None if current is None else current.preview()}
        return value if current is None else current.recorded(value)

    def _inspect_task(self, scene: str, id: int, requester: str) -> Task:
        item = self.records.get(scene, id)
        roles = self._roles(scene, requester)
        if requester in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError('黑名单账号不能读取任务过程')
        if item.account_browser and requester != self.config.owner_qq:
            raise PermissionError('账号浏览过程仅允许当前根主人QQ读取')
        if requester != item.requester and not roles.intersection(self.config.scenes[scene].tasks.manage_roles):
            raise PermissionError('只能读取本人任务过程，或由当前场景任务管理者读取')
        return item

    def _event_text(self, item: Task, event: dict) -> str:
        value = diagnostic_value(event['body'])
        current = self.running.get(item.id)
        if current is not None:
            value = current.recorded(value)
        secrets = credentials(self.config)
        value = redact_record(value, lambda text: redact(text, secrets))
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)

    async def outputs(self, scene: str, id: int, *, requester: str,
                      path: str, offset: int, limit: int) -> dict:
        item = self._inspect_task(scene, id, requester)
        return await self.files.outputs(item, path=path, offset=offset, limit=limit)

    def _event_result(self, item: Task, value: dict) -> dict:
        content = Template((PROMPTS / 'next_task_history.md').read_text()).substitute(
            scene=item.scene, task=item.id, result=json.dumps(value, ensure_ascii=False, allow_nan=False))
        return {'content': content}

    def events(self, scene: str, id: int, *, requester: str, offset: int, snapshot: int | None) -> dict:
        item = self._inspect_task(scene, id, requester)
        page = self.records.event_page(scene, id, offset=offset, snapshot=snapshot)
        previews = []
        for event in page.pop('events'):
            text = self._event_text(item, event)
            previews.append({'event': event['id'], 'kind': event['kind'], 'created': event['created'],
                             'preview': text[:240], 'total_chars': len(text), 'truncated': len(text) > 240})
        return self._event_result(item, {**page, 'previews': previews})

    def read_event(self, scene: str, id: int, event: int, *, requester: str, offset: int) -> dict:
        item = self._inspect_task(scene, id, requester)
        record = self.records.event(scene, id, event)
        text = self._event_text(item, record)
        if offset > len(text):
            raise ValueError(f'Task event offset exceeds its current text projection: offset={offset}, total_chars={len(text)}')
        end = min(offset + 4000, len(text))
        return self._event_result(item, {'event': event, 'kind': record['kind'], 'created': record['created'],
            'offset': offset, 'total_chars': len(text), 'text': text[offset:end],
            'next_offset': end if end < len(text) else None})

    def _notify_live(self, id: int) -> None:
        for listener in self.live_listeners.get(id, ()):
            listener.set()

    def list(self, scene: str, *, status: str = "active", offset: int = 0, limit: int = 20) -> dict:
        items = self.records.list(scene, status=status, offset=offset, limit=limit + 1)
        return {"items": [asdict(item) for item in items[:limit]],
                "next_offset": offset + limit if len(items) > limit else None}

    def _admit_delegate(self, scene: str, requester: str, account_browser: bool) -> None:
        self._can_delegate(scene, requester)
        if account_browser:
            self.task_browser.require_owner(requester)
            if self.records.browser_in_use():
                raise ValueError('专用账号浏览器仍被任务占用；先结束或明确清理原会话')
        timezone = self.config.scene_timezone(scene)
        local = datetime.fromtimestamp(self.store.now(), ZoneInfo(timezone))
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if self.records.count_created(scene, requester, midnight) >= self.config.scenes[scene].tasks.max_daily_tasks:
            raise PermissionError(f"QQ {requester} 今天在本场景的任务次数已达上限")

    async def delegate(self, scene: str, *, requester: str, goal: str,
                       deliverable: str, context: str, account_browser: bool = False,
                       materials: Sequence[MaterialName] = ()) -> dict:
        self._admit_delegate(scene, requester, account_browser)
        selected = tuple(materials)
        stage = create_stage(self.settings, scene) if selected else None
        item: Task | None = None
        original_error: BaseException | None = None
        try:
            copies = [] if stage is None else await finish_file_operation(copy_inputs, stage, self.settings, scene, selected)
            if stage is not None:
                self._admit_delegate(scene, requester, account_browser)
            item = self._register_delegate(scene, requester, goal, deliverable, context, account_browser, selected)
            if stage is not None:
                inputs = publish_inputs(stage, self.settings, scene, item.id, selected)
                self.records.add_event(scene, item.id, 'material_inputs', {'directory': str(inputs), 'files': copies})
        except BaseException as error:
            original_error = error
            if item is not None:
                try:
                    self._finish(item, 'failed', None, ''.join(traceback.format_exception_only(error)).strip())
                except BaseException as record_error:
                    error.add_note(f'Task input failure recording also failed: {type(record_error).__name__}: {record_error}')
            raise
        finally:
            if stage is not None:
                try:
                    remove_stage(stage)
                except OSError as cleanup_error:
                    if original_error is None:
                        if item is not None:
                            cleanup_error.add_note(f'Task #{item.id} is registered with its published inputs; do not submit it again')
                            self._notify(scene)
                        raise
                    original_error.add_note(f'Task input staging cleanup also failed at {stage}: {cleanup_error}')
        self._notify(scene)
        return self.status(scene, item.id)

    def _register_delegate(self, scene: str, requester: str, goal: str, deliverable: str, context: str,
                           account_browser: bool, materials: tuple[str, ...]) -> Task:
        timezone = self.config.scene_timezone(scene)
        local = datetime.fromtimestamp(self.store.now(), ZoneInfo(timezone))
        prompt = Template((PROMPTS / "next_worker.md").read_text()).substitute(
            scene=scene, requester=requester, goal=goal, deliverable=deliverable, context=context,
            timezone=timezone, created_at=local.isoformat())
        if account_browser:
            prompt += '\n\n' + (PROMPTS / 'next_worker_account_browser.md').read_text()
        return self.records.create(scene, requester, goal, deliverable, context, prompt,
                                   account_browser=account_browser, materials=materials)

    async def append(self, scene: str, id: int, *, requester: str, text: str) -> dict:
        if not self.accepting:
            raise RuntimeError("任务执行器正在启动或停止，不能追加运行要求")
        item = self.records.get(scene, id)
        self._can_manage(item, requester)
        if requester in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError('当前黑名单账号不能追加任务要求')
        if item.status not in {"running", "waiting_input"}:
            raise ValueError("追加只适用于已开始的任务；已结束任务用 continue")
        current = self.running[id]
        await current.steer(text)
        self.records.add_event(scene, id, "input", {"requester": requester, "text": text, "mode": "steer"})
        self._notify(scene)
        return {"id": id, "status": "steering_queued", "text": text}

    async def resume(self, scene: str, id: int, *, requester: str, text: str) -> dict:
        async with self.file_changes:
            item = self.records.get(scene, id)
            self._can_manage(item, requester)
            if self.records.workspace_discarded(scene, id):
                raise ValueError('本任务的工作环境已被明确放弃，不能作为原任务续接；请明确新建任务')
            if item.account_browser:
                raise ValueError('账号浏览任务不续接旧工作区；请由主人明确新建独立任务')
            self._can_delegate(scene, requester)
            self._limits(item)
            self.records.requeue(scene, id, text, requester=requester)
            self._notify(scene)
            return self.status(scene, id)

    async def discard_workspace(self, scene: str, id: int, *, requester: str, workspace: str,
                                runtime: str, confirmed: bool) -> dict:
        async with self.file_changes:
            item = self.records.get(scene, id)
            if requester == self.config.bot_qq:
                raise PermissionError('放弃工作环境的操作者必须是实际人类QQ')
            self._can_manage(item, requester)
            if not confirmed:
                raise ValueError('须明确确认放弃本任务工作环境及原生会话；交付副本和执行记录保留')
            if (item.status not in TERMINAL or item.container is not None or item.browser_active or id in self.running):
                raise ValueError('只可放弃终态、容器/账号会话已关闭且宿主已完成收尾的任务环境')
            progress = await self.files.discard(item, requester=requester, workspace=workspace, runtime=runtime)
            return {'task': self.status(scene, id), 'removal': progress,
                    'notice': '工作区与运行目录清理已返回；交付副本、共享原件和执行记录保留，不代表安全擦除或配额实际释放。'}

    async def answer(self, scene: str, id: int, *, requester: str,
                     text: str | None, confirmed: bool | None, question_id: str) -> dict:
        if not self.accepting:
            raise RuntimeError("任务执行器正在启动或停止，不能恢复等待中的任务")
        item = self.records.get(scene, id)
        if requester == self.config.bot_qq:
            raise PermissionError("回答者必须是实际人类 QQ，不用 Bot 冒充回答者")
        if requester in self.config.scene_config(scene).permissions.blacklist:
            raise PermissionError('黑名单账号不能恢复等待回答的任务')
        if item.status != "waiting_input":
            raise ValueError("任务当前没有等待回答的问题")
        if item.account_browser:
            self.task_browser.require_owner(requester)
        if item.question["method"] == "request_help":
            raise ValueError("请在专用浏览器完成当前人工接手；此处不能代答")
        if item.question["id"] != question_id:
            raise ValueError("当前问题已经变化；请重读任务后针对新的问题回答，没有发送这次旧答复")
        current = self.running[id]
        if current.answer_received:
            raise ValueError("当前问题已收到回答或已超时，正在等待执行槽")
        if item.question["method"] == "confirm":
            self._can_manage(item, requester)
            if confirmed is None or text is not None:
                raise ValueError("操作确认必须使用 confirmed 布尔值，普通文字不能替代授权")
            response = {"confirmed": confirmed}
        else:
            if text is None or confirmed is not None:
                raise ValueError("补充信息必须用 text，不是操作授权")
            if item.question["method"] == "select" and text not in item.question["options"]:
                raise ValueError("回答必须是当前问题提供的一个选项")
            response = {"value": text}
        self.records.add_event(scene, id, "answer", {"requester": requester, **response})
        current.submit_answer(response)
        self._notify(scene)
        return {"id": id, "status": "answer_received", "notice": "等待执行槽后继续原任务"}

    async def cancel(self, scene: str, id: int, *, requester: str) -> dict:
        item = self.records.get(scene, id)
        self._can_manage(item, requester)
        if item.status not in {"queued", "running", "waiting_input"}:
            raise ValueError(f"任务已是 {item.status}")
        if item.status != "queued" and not self.accepting:
            raise RuntimeError("宿主正在处理活动任务的启动或中断，请待处理结束后查看结果")
        self.records.add_event(scene, id, "cancel", {"requester": requester})
        if item.status == "queued":
            self._finish(item, "cancelled", None, f"QQ {requester} 取消排队任务")
        elif item.status in {"running", "waiting_input"}:
            current = self.running[id]
            current.interrupt(explicit=True)
            await asyncio.gather(current.job, return_exceptions=True)
        return self.status(scene, id)

    def _limits(self, item: Task) -> Limits:
        costs = self.records.call_costs(item.scene, item.id)
        budget = self.settings.max_cost
        if budget is not None:
            binding = self.config.models.roles.worker
            price = self.config.models.prices[binding.provider][binding.model]
            if any(cost is None or cost["currency"] != price.currency for cost in costs):
                raise ValueError("任务存在未知或不同币种费用，不能继续金额受限的请求")
            budget -= sum((Decimal(cost["amount"]) for cost in costs), Decimal(0))
            if budget <= 0:
                raise ValueError("任务累计模型费用已达上限")
        # One proxy belongs to one explicitly started execution; monetary cost stays cumulative.
        return Limits(self.settings.max_calls, self.settings.max_request_bytes, self.settings.max_response_bytes, budget)

    async def recover(self) -> None:
        """Clean interrupted work even when the platform cannot connect."""
        for item in self.records.containers():
            await self.sandbox.stop_recorded(item.scene, str(item.id), item.container)
            self.records.set_container(item.scene, item.id, None)
        for item in self.records.active():
            self._finish(item, "failed", item.summary, "宿主中断；保留会话与未答问题，须显式继续")
        self.egress.recover()

    async def start(self) -> None:
        self.accepting = True
        self._pump = asyncio.create_task(self._schedule())

    def stop(self) -> None:
        self.accepting = False
        self.changed.set()

    async def close(self) -> None:
        self.stop()
        try:
            if self._pump is not None:
                await self._pump
        finally:
            executions = list(self.running.values())
            for current in executions:
                current.interrupt()
            await asyncio.gather(*(current.job for current in executions), return_exceptions=True)

    async def wait_failure(self) -> None:
        raise await asyncio.shield(self._failure)

    def _fail(self, error: BaseException) -> None:
        self.error = "".join(traceback.format_exception_only(error)).strip()
        self.stop()
        if not self._failure.done():
            self._failure.set_result(error)
        for scene in self.config.scenes:
            self.on_update(scene)

    def _job_done(self, current: TaskExecution, job: asyncio.Task) -> None:
        try:
            if job.cancelled():
                # Cancellation before the coroutine's first step has no finally.
                if current.item.id in self.running:
                    self._finish(current.item, "cancelled" if current.cancelled else "failed",
                                 None, "任务在开始执行前被中断")
                    self._release_execution(current.item.id)
            elif (error := job.exception()) is not None:
                self._fail(error)
        except Exception as error:
            self._fail(error)

    def _room(self, scene: str, *, new_container: bool) -> bool:
        active = [self.records.get(run.item.scene, run.item.id) for run in self.running.values()]
        executing = [item for item in active if item.status == "running"]
        if (len(executing) >= self.settings.max_running
                or sum(item.scene == scene for item in executing) >= self.config.scenes[scene].tasks.max_running):
            return False
        containers = [*active, *(item for item in self.records.containers() if item.id not in self.running)]
        return not new_container or (len(containers) < self.settings.max_containers
            and sum(item.scene == scene for item in containers) < self.settings.max_scene_containers)

    async def _wait_for_slot(self, scene: str) -> None:
        while not self._room(scene, new_container=False):
            self.changed.clear()
            await self.changed.wait()

    def _release_execution(self, id: int) -> None:
        self.running.pop(id)
        self._notify_live(id)

    async def _schedule(self) -> None:
        try:
            while self.accepting:
                self.changed.clear()
                resumable = any(
                    run.input_ready and not run.cancelled
                    and self.records.get(run.item.scene, run.item.id).status == "waiting_input"
                    and self._room(run.item.scene, new_container=False)
                    for run in self.running.values())
                if resumable:
                    # Woken question consumers claim capacity before new work.
                    await self.changed.wait()
                    continue
                for item in self.records.queued():
                    if item.scene not in self.config.scenes:
                        self._finish(item, "failed", None, "任务原场景不在当前根配置中")
                        continue
                    try:
                        self._can_delegate(item.scene, self.records.execution_requester(item))
                        self._limits(item)
                    except (ValueError, PermissionError) as error:
                        self._finish(item, "failed", None, str(error))
                        continue
                    if self._room(item.scene, new_container=True):
                        current = TaskExecution(
                            self.records.start(item.scene, item.id), self.config, self.store,
                            sandbox=self.sandbox, files=self.files, browser=self.task_browser, egress=self.egress,
                            slots=self.slots, memory=self.memory, audio=self.audio, mcp=self.mcp,
                            skills=self.skills, skill_permissions=self.skill_permissions,
                            tool_permissions=self.tool_permissions, data_tools=self.data_tools,
                            active_timeout=self.active_timeout, limits=self._limits,
                            wait_for_slot=self._wait_for_slot, finish=self._finish, release=self._release_execution,
                            notify=self._notify, on_update=self.on_update, notify_live=self._notify_live)
                        self.running[item.id] = current
                        self._notify_live(item.id)
                        current.start().add_done_callback(lambda job, current=current: self._job_done(current, job))
                        self.on_update(item.scene)
                await self.changed.wait()
        except Exception as error:
            self._fail(error)

    def _finish(self, item: Task, status: str, summary: str | None, error: str | None) -> None:
        recent = []
        if status == 'failed' and not item.account_browser:
            page = self.records.event_page(item.scene, item.id, offset=0, snapshot=None)
            recent = [{'event': event['id'], 'kind': event['kind'], 'created': event['created'],
                       'preview': self._event_text(item, event)[:240]} for event in page['events']]
        finished = self.records.finish(item.scene, item.id, status, summary, error)
        files = [file_info(file, self.records) for file in self.records.list_files(item.scene, item.id)]
        body = {"status": status, "summary": summary, "error": error, "files": files,
                "started": finished.started, "ended": finished.ended,
                "cost": cost_summary(self.records.call_costs(item.scene, item.id))}
        notice = f"[任务执行结束] #{item.id}；请求人 QQ {item.requester}；{item.goal}\n" + json.dumps(body, ensure_ascii=False)
        if recent:
            notice += '\n最近已保存过程（预览，不证明操作成功；原文可按event读取）：\n' + json.dumps(recent, ensure_ascii=False)
        elif status == 'failed' and item.account_browser:
            notice += '\n账号浏览过程未公开；仅根主人可按权限读取原事件。'
        self.records.add_event(item.scene, item.id, "finished", body, notice=notice)
        self._notify(item.scene)
