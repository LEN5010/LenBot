import time
import uuid
from typing import Any, Optional, Callable, Awaitable
from len_bot.plugins.models import PluginManifest, PluginPermission
from len_bot.events.models import Event
from len_bot.cognition.models import TaskProposal
from len_bot.actions.models import ActionItem
from len_bot.scheduler.models import TaskItem, TaskStatus

class PluginContext:
    def __init__(self, manifest: PluginManifest, runtime: Any, host: Any):
        self.manifest = manifest
        self._runtime = runtime
        self._host = host

    def has_permission(self, perm: PluginPermission) -> bool:
        return perm in self.manifest.permissions

    async def emit_event(self, event: Event) -> None:
        """Sensory Input: emits an external sensory event into AgentRuntime."""
        if not self.has_permission(PluginPermission.EMIT_EVENT):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'emit_event' permission.")
        self._host.record_plugin_event(self.manifest.id)
        await self._runtime.receive_event(event)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[str]]
    ) -> None:
        """Tool Registration: registers an agentic tool for Cognition."""
        if not self.has_permission(PluginPermission.REGISTER_TOOL):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'register_tool' permission.")
        self._host.register_plugin_tool(
            plugin_id=self.manifest.id,
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            timeout_seconds=self.manifest.timeout_seconds
        )

    def register_action_interceptor(
        self,
        interceptor: Callable[[ActionItem], Awaitable[Optional[ActionItem]]]
    ) -> None:
        """Action Interceptor: registers pre-flight inspection on outbound actions."""
        if not self.has_permission(PluginPermission.INTERCEPT_ACTION):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'intercept_action' permission.")
        self._host.register_action_interceptor(self.manifest.id, interceptor)

    async def schedule_task(self, task_proposal: TaskProposal, scene_id: str) -> str:
        """Scheduled Job: schedules a future task execution via TaskScheduler."""
        if not self.has_permission(PluginPermission.SCHEDULE_TASK):
            raise PermissionError(f"Plugin '{self.manifest.id}' lacks 'schedule_task' permission.")
        now = time.time()
        tid = f"task_{uuid.uuid4().hex[:8]}"
        titem = TaskItem(
            id=tid,
            scene_id=scene_id,
            description=f"[{self.manifest.id}] {task_proposal.description}",
            due_at=now + task_proposal.delay_seconds,
            status=TaskStatus.PENDING,
            payload=task_proposal.payload,
            source_event_id=f"plugin:{self.manifest.id}",
            created_at=now
        )
        await self._runtime.scheduler.schedule_task(titem)
        return tid

class BasePlugin:
    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    async def on_load(self, context: PluginContext) -> None:
        pass

    async def on_unload(self) -> None:
        pass

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass
