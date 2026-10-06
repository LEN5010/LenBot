"""One task execution: Pi session, active time, human input and resource teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
import json
from pathlib import Path
from string import Template
import traceback
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..browser.client import BROWSER_TOOL
from ..media.audio import AudioService, TranscribeArguments
from ..config import HostConfig
from .egress_usage import EgressUsage
from ..memory.service import MemoryService
from ..models.slots import ModelSlots
from ..chat.recall import RecallArguments, recall_chat
from .sandbox import DockerSandbox
from ..tools.skills import Skill, load_task_skills, merge_task_skills
from ..storage.store import Store
from ..browser.tasks import TaskBrowser
from ..browser.files import BrowserOutput, record_browser_output
from .files import TaskFiles
from .inputs import require_inputs
from .live import TaskLiveText
from .materials import finish_file_operation
from ..storage.pool import worker_pool_usage
from .store import Task, TaskStore
from .worker_model import Limits, WorkerModelProxy
from .worker_session import WorkerSession, worker_session

if TYPE_CHECKING:
    from ..tools.external_tools import ExternalTool
    from ..tools.mcp_host import MCPHost


PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


class TaskMCPCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: str = Field(pattern=r"^mcp__[a-zA-Z0-9_-]{1,59}$")
    arguments: dict[str, JsonValue]


class TaskExecution:
    """Own one run's handles; use task-service callbacks for scheduling and completion."""

    def __init__(self, item: Task, config: HostConfig, store: Store, *,
                 sandbox: DockerSandbox, files: TaskFiles, browser: TaskBrowser, egress: EgressUsage,
                 slots: ModelSlots | None, memory: MemoryService | None, audio: AudioService | None,
                 mcp: MCPHost | None, skills: dict[str, tuple[Skill, ...]],
                 skill_permissions: dict[str, Literal["all"] | list[str]],
                 tool_permissions: dict[str, Literal["all"] | list[str]], data_tools: dict[str, list[dict]],
                 active_timeout: Callable[[Task], float], limits: Callable[[Task], Limits],
                 wait_for_slot: Callable[[str], Awaitable[None]],
                 finish: Callable[[Task, str, str | None, str | None], None],
                 release: Callable[[int], None], notify: Callable[[str], None],
                 on_update: Callable[[str], None], notify_live: Callable[[int], None]):
        self.item, self.config, self.store = item, config, store
        self.settings, self.records = config.worker, TaskStore(store)
        self.sandbox, self.files, self.task_browser, self.egress = sandbox, files, browser, egress
        self.slots, self.memory, self.audio, self.mcp = slots, memory, audio, mcp
        self.catalog, self.skill_permissions, self.tool_permissions = skills, skill_permissions, tool_permissions
        self.data_tools = data_tools
        self._active_timeout, self._limits, self._wait_for_slot = active_timeout, limits, wait_for_slot
        self._finish, self._release, self._notify = finish, release, notify
        self.on_update, self._notify_live = on_update, notify_live
        self.job: asyncio.Task
        self._session: WorkerSession | None = None
        self._proxy: WorkerModelProxy | None = None
        self._answer: asyncio.Future[dict] | None = None
        self._timer: asyncio.Timeout | None = None
        self._remaining = 0.0
        self.active_limit = 0.0
        self.input_ready = False
        self.cancelled = False
        self._last_progress: str | None = None
        self._next_progress = 300.0
        self._live_text = TaskLiveText()
        self._skills: tuple[Skill, ...] = ()
        self._mcp_tools: dict[str, ExternalTool] = {}

    def start(self) -> asyncio.Task:
        self.job = asyncio.create_task(self._run())
        return self.job

    def interrupt(self, *, explicit: bool = False) -> None:
        if explicit:
            self.cancelled = True
        if not self.job.cancelling():
            self.job.cancel()

    async def steer(self, text: str) -> None:
        if self._session is None:
            raise ValueError("任务容器仍在启动，请稍后追加")
        await self._session.pi.command("steer", message=text)

    @property
    def answer_received(self) -> bool:
        return self._answer.done()

    def submit_answer(self, response: dict) -> None:
        self._answer.set_result(response)

    def preview(self) -> dict | None:
        return self._live_text.snapshot()

    def recorded[T](self, value: T) -> T:
        return value if self._proxy is None else self._proxy.recorded(value)

    async def _run(self) -> None:
        item = self.item
        binding = self.config.models.roles.worker
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        status, summary, error_text = "failed", None, None
        def bind_proxy(proxy: WorkerModelProxy) -> None:
            self._proxy = proxy
        try:
            await finish_file_operation(worker_pool_usage, self.settings)
            if item.materials:
                await finish_file_operation(require_inputs, self.settings.runtime_root / item.scene / str(item.id) / 'inputs',
                                            tuple(item.materials), self.settings.max_file_bytes)
            if self.settings.mcp:
                if self.mcp is None:
                    raise RuntimeError('任务MCP宿主尚未接入')
                allowed = self.tool_permissions[item.scene]
                self._mcp_tools = {tool.name: tool for tool in self.mcp.tools_for(item.scene)
                                   if allowed == 'all' or tool.name in allowed}
            self.active_limit = self._active_timeout(item)
            async with asyncio.timeout(self.active_limit) as timer:
                self._timer = timer
                self._skills = self.catalog[item.scene]
                if item.account_browser:
                    await self.task_browser.start(item)
                    self._skills = tuple(skill for skill in self._skills if skill.source == 'builtin')
                if (not item.account_browser and self.settings.skills_directory is not None
                        and self.skill_permissions[item.scene] == "all"):
                    workspace = self.settings.workspace_root / item.scene / "tasks" / str(item.id)
                    authored = await asyncio.to_thread(load_task_skills, workspace)
                    self._skills = merge_task_skills(self._skills, authored)
                async with worker_session(
                    self.sandbox, scene=item.scene, task_id=str(item.id),
                    skills=self._skills,
                    input_names=tuple(item.materials),
                    data_tools=self.data_tools[item.scene] + ([BROWSER_TOOL] if item.account_browser else [])
                               + [tool.definition['function'] for tool in self._mcp_tools.values()],
                    task_timeout_seconds=self.active_limit,
                    public_browser=self.settings.public_browser,
                    settings=self.config.model_settings("worker"), provider=binding.provider,
                    context_window_tokens=binding.context_window_tokens, price=price,
                    limits=self._limits(item), model_reasoning=self.settings.model_reasoning,
                    input_support=self.settings.input_support, slots=self.slots,
                    compaction_reserve_tokens=self.settings.compaction_reserve_tokens,
                    compaction_keep_recent_tokens=self.settings.compaction_keep_recent_tokens,
                    egress_settings=self.settings.egress,
                    fake_ip_networks=self.config.network.networks(),
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
                    task_request=self._request,
                ) as session:
                    self._session = session
                    consuming = asyncio.create_task(self._consume())
                    failed = asyncio.create_task(session.wait_failure())
                    try:
                        done, _ = await asyncio.wait({consuming, failed}, return_when=asyncio.FIRST_COMPLETED)
                        session.bridge.raise_if_failed()
                        if session.egress is not None:
                            session.egress.raise_if_failed()
                        if failed in done:
                            await failed
                        summary = await consuming
                    finally:
                        consuming.cancel()
                        failed.cancel()
                        await asyncio.gather(consuming, failed, return_exceptions=True)
                session.bridge.raise_if_failed()
                if session.egress is not None:
                    session.egress.raise_if_failed()
                self.records.set_container(item.scene, item.id, None)
                status = "done"
        except asyncio.CancelledError:
            status = "cancelled" if self.cancelled else "failed"
            error_text = "任务被明确取消" if self.cancelled else "宿主停止，中断活动任务"
        except Exception as error:
            error_text = "".join(traceback.format_exception_only(error)).strip()
        finally:
            closing = asyncio.create_task(self._finalize(status, summary, error_text))
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            closing.result()

    async def _finalize(self, status: str, summary: str | None,
                        error_text: str | None) -> None:
        item = self.item
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
                    await self.task_browser.stop(actual)
                except Exception as error:
                    status = 'failed'
                    error_text = f'{error_text or ""}\n浏览器清理失败：{type(error).__name__}: {error}'
            if self._proxy is not None:
                summary = self._proxy.recorded(summary)
                error_text = self._proxy.recorded(error_text)
            self._finish(item, status, summary, error_text)
        finally:
            try:
                self.egress.release_task(item.scene, item.id)
            finally:
                self._release(item.id)

    async def _consume(self) -> str:
        item, session = self.item, self._session
        # PiRpc's reader already drains stdout while prompt is being accepted.
        environment = Template((PROMPTS / "next_worker_environment.md").read_text()).substitute(
            facts=json.dumps({
                "public_network": session.egress is not None,
                "proxy": None if session.egress is None else f"http://127.0.0.1:{session.egress.port}",
                "task_traffic": self.egress.status(item.scene, item.id),
                "scene_today_traffic": self.egress.status(item.scene),
                "active_timeout_seconds": self.active_limit,
                "model_call_limit": self.settings.max_calls,
                "data_tools": [tool["name"] for tool in self.data_tools[item.scene]] + (["account_browser"] if item.account_browser else [])
                              + list(self._mcp_tools),
                "public_browser": None if session.browser_cli_version is None else {
                    "command": "lenbot-browser", "cli_version": session.browser_cli_version,
                    "session": "public", "profile": "in-memory",
                    "output_directory": "/workspace/out/browser",
                    "startup_check": "CLI and executable only; browser opens on demand",
                },
                "skills": [{"name": skill.name, "source": skill.source,
                            "path": skill.container_path + "/SKILL.md"}
                           for skill in self._skills if not skill.disable_model_invocation],
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
        # Pi 0.87.1 acknowledges prompt preflight with success:true and no data.
        # Completion is reported by the event stream, not by this response.
        await session.pi.command("prompt", message=request)
        final: dict | None = None
        while True:
            deadline = self._timer.when()
            if deadline is not None:
                elapsed = self.active_limit - max(0, deadline - asyncio.get_running_loop().time())
                if elapsed >= self._next_progress:
                    self._progress(self._last_progress or "任务仍在执行，尚未报告阶段说明")
                    self._next_progress = elapsed + 600
            try:
                record = await asyncio.wait_for(session.pi.next_event(), timeout=60)
            except TimeoutError:
                continue
            body = record.body
            if record.type == "message_update":
                if self._live_text.update(body["assistantMessageEvent"]):
                    self._notify_live(item.id)
                continue
            if record.type in {"tool_execution_update", "bash_execution_update"}:
                continue
            self.records.add_event(item.scene, item.id, "native", session.proxy.recorded(body))
            self.on_update(item.scene)
            if record.type == "message_start":
                if self._live_text.start(body["message"]):
                    self._notify_live(item.id)
            elif record.type == "message_end" and body["message"]["role"] == "assistant":
                if self._live_text.finish(body["message"]):
                    self._notify_live(item.id)
                final = body["message"]
            elif record.type == "entry_appended":
                entry = body["entry"]
                if entry.get("customType") == "lenbot_progress":
                    self._progress(entry["data"]["text"])
            elif record.type == "extension_ui_request" and body["method"] in {"input", "confirm", "select", "editor"}:
                await self._question(body)
            elif record.type in {"auto_retry_start", "summarization_retry_scheduled"}:
                raise RuntimeError(f"Pi 发起了未启用的重试：{body!r}")
            elif record.type == "agent_settled":
                if final is None:
                    raise RuntimeError("Pi 停止但没有最终助手消息")
                if final["stopReason"] != "stop":
                    raise RuntimeError(f"Pi 未正常完成：{json.dumps(final, ensure_ascii=False)}")
                return "\n".join(part["text"] for part in final["content"] if part["type"] == "text")

    def _progress(self, text: str) -> None:
        text = self._proxy.recorded(text)
        self._last_progress = text
        item = self.item
        self.records.add_event(item.scene, item.id, "progress", {"text": text},
                               notice=f"[任务进度] #{item.id}；{item.goal}\n{text}")
        self._notify(item.scene)

    def _pause_input(self, question: dict) -> None:
        loop = asyncio.get_running_loop()
        deadline = self._timer.when()
        if deadline is None:
            raise RuntimeError("任务已有人工等待，不能同时发起另一项")
        self._remaining = deadline - loop.time()
        if self._remaining <= 0:
            raise TimeoutError("任务活动执行时长已达上限")
        self._timer.reschedule(None)
        self.input_ready = False
        self.records.set_question(self.item.scene, self.item.id, question)
        self._notify(self.item.scene)

    async def _resume_input(self) -> None:
        if self.cancelled or self.job.cancelling():
            raise asyncio.CancelledError
        self.input_ready = True
        self._notify(self.item.scene)
        await self._wait_for_slot(self.item.scene)
        if self.cancelled or self.job.cancelling():
            raise asyncio.CancelledError
        self.records.set_question(self.item.scene, self.item.id, None)
        self.input_ready = False
        self._timer.reschedule(asyncio.get_running_loop().time() + self._remaining)
        self._notify(self.item.scene)

    async def _question(self, body: dict) -> None:
        item = self.item
        if (not isinstance(body.get("title"), str)
                or body["method"] == "confirm" and not isinstance(body.get("message"), str)):
            raise ValueError(f"Pi 问题缺少原文：{body!r}")
        if body["method"] == "select" and (not isinstance(body.get("options"), list)
                or not all(isinstance(option, str) for option in body["options"])):
            raise ValueError(f"Pi 选择题选项无效：{body!r}")
        self._answer = asyncio.get_running_loop().create_future()
        self._pause_input(body)
        self.records.add_event(item.scene, item.id, "question", body,
            notice=f"[任务{'操作确认' if body['method'] == 'confirm' else '提问'}] #{item.id}；"
                   f"请求人 {item.requester}\n{json.dumps(body, ensure_ascii=False)}")
        self._notify(item.scene)
        try:
            response = await asyncio.wait_for(self._answer, timeout=self.settings.input_timeout_seconds)
        except TimeoutError:
            response = {"confirmed": False} if body["method"] == "confirm" else {"cancelled": True}
            self.records.add_event(item.scene, item.id, "answer_timeout", response)
        await self._resume_input()
        await self._session.pi.respond_ui(body["id"], **response)
        self._notify(item.scene)

    async def _request(self, path: str, raw: bytes) -> dict:
        if path == '/task/browser-file':
            if not self.settings.public_browser:
                raise PermissionError('当前任务未开放公共浏览器')
            result = record_browser_output(self.settings, self.records, self.item, BrowserOutput.parse(raw))
            self._notify(self.item.scene)
            return result
        if path == '/task/mcp':
            try:
                call = TaskMCPCall.model_validate_json(raw)
            except ValidationError as error:
                raise ValueError(f'Invalid task MCP request: {raw[:500]!r}; {error}') from error
            if not self.settings.mcp or call.name not in self._mcp_tools:
                raise PermissionError(f'本次任务未开放MCP工具 {call.name}')
            if self.mcp is None:
                raise RuntimeError('任务MCP宿主尚未接入')
            tools = {tool.name: tool for tool in self.mcp.tools_for(self.item.scene)}
            if call.name not in tools or tools[call.name] != self._mcp_tools[call.name]:
                raise RuntimeError(f'MCP工具 {call.name} 当前不可用或定义已变化；未调用，不更新本次工具集合')
            content = await tools[call.name].call(self.item.scene, call.arguments)
            return {'content': content}
        if path == '/task/account-browser':
            item = self.records.get(self.item.scene, self.item.id)
            return await self.task_browser.execute(
                item, raw, pause_input=self._pause_input, resume_input=self._resume_input)
        if path in {"/task/recall-chat", "/task/memory", "/task/transcribe"}:
            name = {"/task/recall-chat": "recall_chat", "/task/memory": "memory", "/task/transcribe": "transcribe"}[path]
            scene = self.item.scene
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
            return {"task": self.egress.status(self.item.scene, self.item.id),
                    "scene_today": self.egress.status(self.item.scene)}
        return await self.files.deliver(self.item, self._session.sandbox, raw)
