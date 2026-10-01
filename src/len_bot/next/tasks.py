"""Scene-owned work tasks: scheduling, native Pi questions and copied files."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import partial
import json
from pathlib import Path
from string import Template
import traceback
from typing import Literal, TYPE_CHECKING
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from .config import HostConfig
from .audio import AudioService, TranscribeArguments
from .account_browser import AccountBrowser, BrowserAction, BROWSER_TOOL
from .identity import roles_for
from .egress_usage import EgressUsage
from .model_slots import ModelSlots
from .memory import MemoryService
from .pricing import cost_summary
from .recall import RecallArguments, recall_chat
from .sandbox import DockerSandbox, DockerSettings
from .skills import Skill, load_task_skills, merge_task_skills
from .store import Store
from .task_live import TaskLiveText
from .tasks_store import TERMINAL, Task, TaskFile, TaskStore
from .worker_model import Limits, WorkerModelProxy
from .worker_session import WorkerSession, worker_session
from .task_materials import MaterialName, finish_file_operation
from .task_inputs import copy_inputs, create_stage, publish_inputs, remove_stage, require_inputs
from .task_storage import discard_task_trees
from .operations import credentials, diagnostic_value, redact, redact_record

if TYPE_CHECKING:
    from .external_tools import ExternalTool
    from .mcp_host import MCPHost


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class DeliverFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=240)
    note: str

    @field_validator("name")
    @classmethod
    def filename(cls, value: str) -> str:
        if value in {".", ".."} or any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError("name must be a filename, not a path")
        return value


class TaskMCPCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: str = Field(pattern=r"^mcp__[a-zA-Z0-9_-]{1,59}$")
    arguments: dict[str, JsonValue]


@dataclass
class RunningTask:
    item: Task
    job: asyncio.Task | None = None
    session: WorkerSession | None = None
    proxy: WorkerModelProxy | None = field(default=None, repr=False)
    answer: asyncio.Future[dict] | None = None
    input_ready: bool = False
    timer: asyncio.Timeout | None = None
    remaining: float = 0
    active_limit: float = 0
    cancelled: bool = False
    last_progress: str | None = None
    next_progress: float = 300
    live_text: TaskLiveText = field(default_factory=TaskLiveText)
    skills: tuple[Skill, ...] = ()
    mcp_tools: dict[str, ExternalTool] = field(default_factory=dict)


def file_info(file: TaskFile, records: TaskStore) -> dict:
    return {"id": file.id, "task_id": file.task_id, "name": file.name,
            "size": file.size, "note": file.note, "status": "registered",
            "upload": records.latest_file_upload(file)}


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
        self.running: dict[int, RunningTask] = {}
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
            self._browser_owner(requester)
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
                 "preview": None if current is None else current.live_text.snapshot()}
        return value if current is None or current.proxy is None else current.proxy.recorded(value)

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
        if current is not None and current.proxy is not None:
            value = current.proxy.recorded(value)
        secrets = credentials(self.config)
        value = redact_record(value, lambda text: redact(text, secrets))
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)

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

    def _browser_owner(self, requester: str) -> None:
        if self.config.owner_qq is None or requester != self.config.owner_qq:
            raise PermissionError('账号浏览仅允许根配置的主人QQ')
        if self.browser is None or self.browser.settings.browser_instance_id is None:
            raise ValueError('账号浏览服务尚未配置或未明确绑定专用浏览器')

    def _admit_delegate(self, scene: str, requester: str, account_browser: bool) -> None:
        self._can_delegate(scene, requester)
        if account_browser:
            self._browser_owner(requester)
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
        if current.session is None:
            raise ValueError("任务容器仍在启动，请稍后追加")
        await current.session.pi.command("steer", message=text)
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
            expected_workspace = self.settings.workspace_root / scene / 'tasks' / str(id)
            expected_runtime = self.settings.runtime_root / scene / str(id)
            if workspace != str(expected_workspace) or runtime != str(expected_runtime):
                raise ValueError(f'Task removal roots differ from the current worker; expected='
                                 f'{(str(expected_workspace), str(expected_runtime))!r}; received={(workspace, runtime)!r}')
            for file in self.records.registered_files():
                recorded = Path(file.path)
                actual = recorded.resolve(strict=False)
                if any(path.is_relative_to(root) for path in (recorded, actual)
                       for root in (expected_workspace, expected_runtime)):
                    raise ValueError(f'Registered delivery would be removed by these current task roots; '
                                     f'scene={file.scene!r}, task={file.task_id}, file={file.id}, path={file.path!r}; '
                                     'retain/move the actual delivery explicitly before discarding this environment')
            self.records.add_event(scene, id, 'workspace_discard', {'requester': requester,
                'workspace': workspace, 'runtime': runtime, 'notice': '明确放弃此任务环境，不再续接；不是物理删除成功回执'})
            self._notify(scene)
            progress = {'complete': False, 'removed': [], 'absent': [], 'active_root': None, 'error': None}
            original_error: BaseException | None = None
            try:
                await finish_file_operation(discard_task_trees, expected_workspace, expected_runtime, progress)
            except BaseException as error:
                original_error = error
                progress['error'] = ''.join(traceback.format_exception_only(error)).strip()
                error.add_note(f'Task #{id} environment is permanently discarded; partial removal={progress!r}')
                raise
            finally:
                try:
                    self.records.add_event(scene, id, 'workspace_discard_result', progress)
                    self._notify(scene)
                except BaseException as record_error:
                    if original_error is None:
                        record_error.add_note(f'Task #{id} environment is discarded; filesystem result={progress!r}')
                        raise
                    original_error.add_note(f'Workspace removal result recording also failed: {type(record_error).__name__}: {record_error}')
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
            self._browser_owner(requester)
        if item.question["method"] == "request_help":
            raise ValueError("请在专用浏览器完成当前人工接手；此处不能代答")
        if item.question["id"] != question_id:
            raise ValueError("当前问题已经变化；请重读任务后针对新的问题回答，没有发送这次旧答复")
        current = self.running[id]
        if current.answer.done():
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
        current.answer.set_result(response)
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
            current.cancelled = True
            if not current.job.cancelling():
                current.job.cancel()
            await asyncio.gather(current.job, return_exceptions=True)
        return self.status(scene, id)

    def _limits(self, item: Task) -> Limits:
        costs = self.records.call_costs(item.scene, item.id)
        calls = self.settings.max_calls - len(costs)
        if calls <= 0:
            raise ValueError("任务累计模型调用次数已达上限")
        budget = self.settings.max_cost
        if budget is not None:
            binding = self.config.models.roles.worker
            price = self.config.models.prices[binding.provider][binding.model]
            if any(cost is None or cost["currency"] != price.currency for cost in costs):
                raise ValueError("任务存在未知或不同币种费用，不能继续金额受限的请求")
            budget -= sum((Decimal(cost["amount"]) for cost in costs), Decimal(0))
            if budget <= 0:
                raise ValueError("任务累计模型费用已达上限")
        return Limits(calls, self.settings.max_request_bytes, self.settings.max_response_bytes, budget)

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
            jobs = [current.job for current in self.running.values()]
            for job in jobs:
                if not job.cancelling():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def wait_failure(self) -> None:
        raise await asyncio.shield(self._failure)

    def _fail(self, error: BaseException) -> None:
        self.error = "".join(traceback.format_exception_only(error)).strip()
        self.stop()
        if not self._failure.done():
            self._failure.set_result(error)
        for scene in self.config.scenes:
            self.on_update(scene)

    def _job_done(self, current: RunningTask, job: asyncio.Task) -> None:
        try:
            if job.cancelled():
                # Cancellation before the coroutine's first step has no finally.
                if current.item.id in self.running:
                    self._finish(current.item, "cancelled" if current.cancelled else "failed",
                                 None, "任务在开始执行前被中断")
                    self.running.pop(current.item.id)
                    self._notify_live(current.item.id)
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
                        current = RunningTask(self.records.start(item.scene, item.id))
                        self.running[item.id] = current
                        self._notify_live(item.id)
                        current.job = asyncio.create_task(self._run(current))
                        current.job.add_done_callback(lambda job, current=current: self._job_done(current, job))
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

    async def _run(self, current: RunningTask) -> None:
        item = current.item
        binding = self.config.models.roles.worker
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        status, summary, error_text = "failed", None, None
        def bind_proxy(proxy: WorkerModelProxy) -> None:
            current.proxy = proxy
        try:
            if item.materials:
                await finish_file_operation(require_inputs, self.settings.runtime_root / item.scene / str(item.id) / 'inputs',
                                            tuple(item.materials), self.settings.max_file_bytes)
            if self.settings.mcp:
                if self.mcp is None:
                    raise RuntimeError('任务MCP宿主尚未接入')
                allowed = self.tool_permissions[item.scene]
                current.mcp_tools = {tool.name: tool for tool in self.mcp.tools_for(item.scene)
                                     if allowed == 'all' or tool.name in allowed}
            current.active_limit = self.active_timeout(item)
            async with asyncio.timeout(current.active_limit) as timer:
                current.timer = timer
                current.skills = self.skills[item.scene]
                if item.account_browser:
                    self._browser_owner(item.requester)
                    async with self.browser.lock:
                        if self.records.browser_in_use() or await self.browser.sessions():
                            raise ValueError('专用账号浏览器仍被占用，未启动本任务')
                        self.records.browser_binding(item.scene, item.id, active=True, session=None)
                        session_id = await self.browser.start()
                        self.records.browser_binding(item.scene, item.id, active=True, session=session_id)
                    self.records.add_event(item.scene, item.id, 'browser_started', {'session_id': session_id,
                        'socket': str(self.browser.settings.socket), 'browser_instance_id': self.browser.settings.browser_instance_id})
                    current.skills = tuple(skill for skill in current.skills if skill.source == 'builtin')
                if (not item.account_browser and self.settings.skills_directory is not None
                        and self.skill_permissions[item.scene] == "all"):
                    workspace = self.settings.workspace_root / item.scene / "tasks" / str(item.id)
                    authored = await asyncio.to_thread(load_task_skills, workspace)
                    current.skills = merge_task_skills(current.skills, authored)
                async with worker_session(
                    self.sandbox, scene=item.scene, task_id=str(item.id),
                    skills=current.skills,
                    input_names=tuple(item.materials),
                    data_tools=self.data_tools[item.scene] + ([BROWSER_TOOL] if item.account_browser else [])
                               + [tool.definition['function'] for tool in current.mcp_tools.values()],
                    task_timeout_seconds=current.active_limit,
                    public_browser=self.settings.public_browser,
                    settings=self.config.model_settings("worker"), provider=binding.provider,
                    context_window_tokens=binding.context_window_tokens, price=price,
                    limits=self._limits(item), model_reasoning=self.settings.model_reasoning,
                    input_support=self.settings.input_support, slots=self.slots,
                    compaction_reserve_tokens=self.settings.compaction_reserve_tokens,
                    compaction_keep_recent_tokens=self.settings.compaction_keep_recent_tokens,
                    egress_settings=self.settings.egress,
                    egress_bytes_per_second=(
                        self.settings.egress.bytes_per_second
                        if self.config.scenes[item.scene].tasks.egress_bytes_per_second is None
                        else self.config.scenes[item.scene].tasks.egress_bytes_per_second
                    ),
                    before_bytes=partial(self.egress.before_bytes, item.scene, item.id),
                    on_bytes=partial(self.egress.on_bytes, item.scene, item.id),
                    on_connection=partial(self.egress.on_connection, item.scene, item.id),
                    start_call=lambda facts: self.records.start_call(item.scene, item.id, facts),
                    finish_call=self.records.finish_call,
                    on_proxy=bind_proxy,
                    on_container=lambda container: self.records.set_container(item.scene, item.id, container),
                    task_request=lambda path, raw: self._request(current, path, raw),
                ) as session:
                    current.session = session
                    consuming = asyncio.create_task(self._consume(current))
                    failed = asyncio.create_task(session.wait_failure())
                    try:
                        done, _ = await asyncio.wait({consuming, failed}, return_when=asyncio.FIRST_COMPLETED)
                        if failed in done:
                            await failed
                        summary = await consuming
                    finally:
                        consuming.cancel()
                        failed.cancel()
                        await asyncio.gather(consuming, failed, return_exceptions=True)
                self.records.set_container(item.scene, item.id, None)
                status = "done"
        except asyncio.CancelledError:
            status = "cancelled" if current.cancelled else "failed"
            error_text = "任务被明确取消" if current.cancelled else "宿主停止，中断活动任务"
        except Exception as error:
            error_text = "".join(traceback.format_exception_only(error)).strip()
        finally:
            closing = asyncio.create_task(self._finalize(current, status, summary, error_text))
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            closing.result()

    async def _finalize(self, current: RunningTask, status: str, summary: str | None,
                        error_text: str | None) -> None:
        item = current.item
        try:
            actual = self.records.get(item.scene, item.id)
            if actual.container is not None:
                try:
                    await self.sandbox.stop_recorded(item.scene, str(item.id), actual.container)
                    self.records.set_container(item.scene, item.id, None)
                except Exception as error:
                    status = "failed"
                    error_text = f"{error_text or ''}\n容器清理失败：{type(error).__name__}: {error}"
            actual = self.records.get(item.scene, item.id)
            if actual.browser_active:
                try:
                    if actual.browser_session is None:
                        raise RuntimeError('浏览器创建结果未知，保留配置占用；请从能力页检查实际会话并明确清理')
                    result = await self.browser.stop(actual.browser_session)
                    self.records.browser_binding(item.scene, item.id, active=False, session=None)
                    self.records.add_event(item.scene, item.id, 'browser_stopped', result)
                except Exception as error:
                    status = 'failed'
                    error_text = f'{error_text or ""}\n浏览器清理失败：{type(error).__name__}: {error}'
            if current.proxy is not None:
                summary = current.proxy.recorded(summary)
                error_text = current.proxy.recorded(error_text)
            self._finish(item, status, summary, error_text)
        finally:
            try:
                self.egress.release_task(item.scene, item.id)
            finally:
                self.running.pop(item.id)
                self._notify_live(item.id)

    async def _consume(self, current: RunningTask) -> str:
        item, session = current.item, current.session
        # PiRpc's reader already drains stdout while prompt is being accepted.
        environment = Template((PROMPTS / "next_worker_environment.md").read_text()).substitute(
            facts=json.dumps({
                "public_network": session.egress is not None,
                "proxy": None if session.egress is None else f"http://127.0.0.1:{session.egress.port}",
                "task_traffic": self.egress.status(item.scene, item.id),
                "scene_today_traffic": self.egress.status(item.scene),
                "active_timeout_seconds": current.active_limit,
                "data_tools": [tool["name"] for tool in self.data_tools[item.scene]] + (["account_browser"] if item.account_browser else [])
                              + list(current.mcp_tools),
                "public_browser": None if session.browser_cli_version is None else {
                    "command": "lenbot-browser", "cli_version": session.browser_cli_version,
                    "session": "public", "profile": "in-memory",
                    "output_directory": "/workspace/out/browser",
                    "startup_check": "CLI and executable only; browser opens on demand",
                },
                "skills": [{"name": skill.name, "source": skill.source,
                            "path": skill.container_path + "/SKILL.md"}
                           for skill in current.skills if not skill.disable_model_invocation],
            }, ensure_ascii=False, allow_nan=False),
        )
        request = item.input + "\n\n" + environment
        continuation = self.records.continuation(item)
        if continuation is not None:
            request += '\n\n' + Template((PROMPTS / 'next_worker_continuation.md').read_text()).substitute(
                facts=json.dumps({'task_id': item.id, 'scene': item.scene, 'original_requester': item.requester,
                    'original_goal': item.goal, 'original_deliverable': item.deliverable,
                    'original_context': item.context, 'original_created_at': item.created,
                    'current_operator': continuation.requester, 'current_text': continuation.text},
                    ensure_ascii=False, allow_nan=False))
        if item.materials:
            request += '\n\n' + Template((PROMPTS / 'next_worker_materials.md').read_text()).substitute(
                materials=json.dumps([{'name': name, 'path': f'/inputs/{name}'} for name in item.materials],
                                     ensure_ascii=False, allow_nan=False))
        if await session.pi.prompt(request) == "handled":
            raise RuntimeError("Pi 处理了输入但未开始执行任务")
        final: dict | None = None
        while True:
            deadline = current.timer.when()
            if deadline is not None:
                elapsed = current.active_limit - max(0, deadline - asyncio.get_running_loop().time())
                if elapsed >= current.next_progress:
                    self._progress(current, current.last_progress or "任务仍在执行，尚未报告阶段说明")
                    current.next_progress = elapsed + 600
            try:
                record = await asyncio.wait_for(session.pi.next_event(), timeout=60)
            except TimeoutError:
                continue
            body = record.body
            if record.type == "message_update":
                if current.live_text.update(body["assistantMessageEvent"]):
                    self._notify_live(item.id)
                continue
            if record.type in {"tool_execution_update", "bash_execution_update"}:
                continue
            self.records.add_event(item.scene, item.id, "native", session.proxy.recorded(body))
            self.on_update(item.scene)
            if record.type == "message_start":
                if current.live_text.start(body["message"]):
                    self._notify_live(item.id)
            elif record.type == "message_end" and body["message"]["role"] == "assistant":
                if current.live_text.finish(body["message"]):
                    self._notify_live(item.id)
                final = body["message"]
            elif record.type == "entry_appended":
                entry = body["entry"]
                if entry.get("customType") == "lenbot_progress":
                    self._progress(current, entry["data"]["text"])
            elif record.type == "extension_ui_request" and body["method"] in {"input", "confirm", "select", "editor"}:
                await self._question(current, body)
            elif record.type in {"auto_retry_start", "summarization_retry_scheduled"}:
                raise RuntimeError(f"Pi 发起了未启用的重试：{body!r}")
            elif record.type == "agent_settled":
                if final is None:
                    raise RuntimeError("Pi 停止但没有最终助手消息")
                if final["stopReason"] != "stop":
                    raise RuntimeError(f"Pi 未正常完成：{json.dumps(final, ensure_ascii=False)}")
                return "\n".join(part["text"] for part in final["content"] if part["type"] == "text")

    def _progress(self, current: RunningTask, text: str) -> None:
        text = current.proxy.recorded(text)
        current.last_progress = text
        item = current.item
        self.records.add_event(item.scene, item.id, "progress", {"text": text},
                               notice=f"[任务进度] #{item.id}；{item.goal}\n{text}")
        self._notify(item.scene)

    def _pause_input(self, current: RunningTask, question: dict) -> None:
        loop = asyncio.get_running_loop()
        deadline = current.timer.when()
        if deadline is None:
            raise RuntimeError("任务已有人工等待，不能同时发起另一项")
        current.remaining = deadline - loop.time()
        if current.remaining <= 0:
            raise TimeoutError("任务活动执行时长已达上限")
        current.timer.reschedule(None)
        current.input_ready = False
        self.records.set_question(current.item.scene, current.item.id, question)
        self._notify(current.item.scene)

    async def _resume_input(self, current: RunningTask) -> None:
        if current.cancelled or current.job.cancelling():
            raise asyncio.CancelledError
        current.input_ready = True
        self._notify(current.item.scene)
        while not self._room(current.item.scene, new_container=False):
            self.changed.clear()
            await self.changed.wait()
        if current.cancelled or current.job.cancelling():
            raise asyncio.CancelledError
        self.records.set_question(current.item.scene, current.item.id, None)
        current.input_ready = False
        current.timer.reschedule(asyncio.get_running_loop().time() + current.remaining)
        self._notify(current.item.scene)

    async def _question(self, current: RunningTask, body: dict) -> None:
        item = current.item
        if (not isinstance(body.get("title"), str)
                or body["method"] == "confirm" and not isinstance(body.get("message"), str)):
            raise ValueError(f"Pi 问题缺少原文：{body!r}")
        if body["method"] == "select" and (not isinstance(body.get("options"), list)
                or not all(isinstance(option, str) for option in body["options"])):
            raise ValueError(f"Pi 选择题选项无效：{body!r}")
        current.answer = asyncio.get_running_loop().create_future()
        self._pause_input(current, body)
        self.records.add_event(item.scene, item.id, "question", body,
            notice=f"[任务{'操作确认' if body['method'] == 'confirm' else '提问'}] #{item.id}；"
                   f"请求人 QQ {item.requester}\n{json.dumps(body, ensure_ascii=False)}")
        self._notify(item.scene)
        try:
            response = await asyncio.wait_for(current.answer, timeout=self.settings.input_timeout_seconds)
        except TimeoutError:
            response = {"confirmed": False} if body["method"] == "confirm" else {"cancelled": True}
            self.records.add_event(item.scene, item.id, "answer_timeout", response)
        await self._resume_input(current)
        await current.session.pi.respond_ui(body["id"], **response)
        self._notify(item.scene)

    async def _request(self, current: RunningTask, path: str, raw: bytes) -> dict:
        if path == '/task/mcp':
            try:
                call = TaskMCPCall.model_validate_json(raw)
            except ValidationError as error:
                raise ValueError(f'Invalid task MCP request: {raw[:500]!r}; {error}') from error
            if not self.settings.mcp or call.name not in current.mcp_tools:
                raise PermissionError(f'本次任务未开放MCP工具 {call.name}')
            if self.mcp is None:
                raise RuntimeError('任务MCP宿主尚未接入')
            tools = {tool.name: tool for tool in self.mcp.tools_for(current.item.scene)}
            if call.name not in tools or tools[call.name] != current.mcp_tools[call.name]:
                raise RuntimeError(f'MCP工具 {call.name} 当前不可用或定义已变化；未调用，不更新本次工具集合')
            content = await tools[call.name].call(current.item.scene, call.arguments)
            return {'content': content}
        if path == '/task/account-browser':
            item = self.records.get(current.item.scene, current.item.id)
            if not item.account_browser:
                raise PermissionError('普通任务不能获得账号浏览能力，请另建主人授权任务')
            self._browser_owner(item.requester)
            if not item.browser_active or item.browser_session is None:
                raise RuntimeError('此任务没有已确认的账号浏览会话')
            action = BrowserAction.model_validate_json(raw)
            if action.method == 'screenshot' and self.settings.input_support != 'text-image':
                raise ValueError('当前工作模型未配置图像输入，不能向它提供浏览器截图')
            if action.method == 'request_help':
                # The host's existing input wait limit governs human time, not active execution.
                params = {**action.params, 'timeout_ms': int(self.settings.input_timeout_seconds * 1000)}
                action = BrowserAction(method=action.method, params=params)
                question = {'method': 'request_help', 'title': params['prompt'], 'params': params}
                self._pause_input(current, question)
                self.records.add_event(item.scene, item.id, 'question', question,
                    notice=f"[任务浏览器接手] #{item.id}；请在专用浏览器完成：{params['prompt']}")
                try:
                    result = await self.browser.execute(item.browser_session, action)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._resume_input(current)
                    raise
                else:
                    self.records.add_event(item.scene, item.id, 'answer', result)
                    await self._resume_input(current)
            else:
                result = await self.browser.execute(item.browser_session, action)
            if action.method == 'screenshot':
                if not isinstance(result.get('image_base64'), str):
                    raise ValueError(f'Browser screenshot lacks image_base64: {result!r}')
                return {'content': json.dumps({k:v for k,v in result.items() if k != 'image_base64'}, ensure_ascii=False),
                        'image': {'data': result['image_base64'], 'mimeType': 'image/png'}}
            return {'content': json.dumps(result, ensure_ascii=False)}
        if path in {"/task/recall-chat", "/task/memory", "/task/transcribe"}:
            name = {"/task/recall-chat": "recall_chat", "/task/memory": "memory", "/task/transcribe": "transcribe"}[path]
            scene = current.item.scene
            if name not in {tool["name"] for tool in self.data_tools[scene]}:
                raise PermissionError(f"当前场景的任务未开放 {name}")
            if name == "transcribe":
                if self.audio is None:
                    raise RuntimeError("任务语音服务尚未绑定")
                arguments = TranscribeArguments.model_validate_json(raw)
                content = await self.audio.transcribe(scene, arguments)
            elif name == "recall_chat":
                try:
                    arguments = RecallArguments.model_validate_json(raw)
                except ValidationError as error:
                    raise ValueError(f"Invalid task recall_chat request: {raw[:500]!r}; {error}") from error
                content = recall_chat(self.store, scene, self.config.scene_timezone(scene), arguments)
            else:
                try:
                    arguments = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise ValueError(f"Invalid task memory JSON: {raw[:500]!r}; {error}") from error
                content = await self.memory.execute(scene, arguments)
            return {"content": content}
        if path == "/task/network":
            try:
                request = json.loads(raw)
            except ValueError as error:
                raise ValueError(f"Invalid task network request: {raw[:500]!r}; {error}") from error
            if request != {}:
                raise ValueError(f"Task network status expects an empty object: {raw[:500]!r}")
            return {"task": self.egress.status(current.item.scene, current.item.id),
                    "scene_today": self.egress.status(current.item.scene)}
        arguments = DeliverFile.model_validate_json(raw)
        item = current.item
        destination = self.settings.delivery_root / item.scene / str(item.id)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / uuid4().hex
        try:
            await self.sandbox.copy_out(current.session.sandbox, arguments.path, target,
                                        max_bytes=self.settings.max_file_bytes)
            file = self.records.add_file(item.scene, item.id, name=arguments.name, path=str(target),
                                         size=target.stat().st_size, note=arguments.note)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        self.records.add_event(item.scene, item.id, "file", file_info(file, self.records))
        self._notify(item.scene)
        return file_info(file, self.records)
