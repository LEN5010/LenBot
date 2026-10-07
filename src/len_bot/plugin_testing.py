"""Public local plugin harness: real lifecycle and KV, captured simulated delivery."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import asyncio
import shutil
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

from .next.config import HostConfig
from .next.platform.identity import validate_scene
from .next.platform.messages import ChatMessage, Sender, Segment
from .plugin import Content, Image, Sent, Text
from .next.plugins.host import PluginHost
from .next.plugins.data import write_version
from .next.plugins.manifest import parse_manifest
from .next.plugins.store import PluginStore
from .next.storage.store import Store


@dataclass(frozen=True)
class Delivery:
    plugin: str
    scene: str
    parts: tuple[Content, ...]
    reply_to: str | None
    status: str = 'simulated'

    @property
    def text(self) -> str:
        return ''.join(item.text for item in self.parts if isinstance(item, Text))


class _LocalHost(PluginHost):
    def __init__(self, config: HostConfig, deliveries: list[Delivery], *, now: Callable[[], float], model_enabled: bool):
        super().__init__(config, core_tools=set(), now=now)
        self.model_enabled = model_enabled
        self.deliveries = deliveries
        self.messages: list[ChatMessage] = []
        self.runtime = SimpleNamespace(store=Store(config.database), mcp=None, slots=None, runners={
            scene: SimpleNamespace(state=SimpleNamespace(paused=False)) for scene in config.scenes})

    async def send_parts(self, plugin: str, scene: str, parts: Sequence[Content], reply_to: str | None) -> Sent:
        self._speaking(plugin, scene)
        captured = tuple(Text(self.redact(plugin, item.text)) if isinstance(item, Text) else
                         Image(item.data, self.redact(plugin, item.description)) if isinstance(item, Image) else item
                         for item in parts)
        self.deliveries.append(Delivery(plugin, scene, captured, reply_to))
        return Sent('simulated', 'Captured locally; no platform message was sent')

    def recent_messages(self, plugin: str, scene: str, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        return [item for item in self.messages if item.scene == scene][-limit:]

    def messages_between(self, plugin: str, scene: str, after: float, before: float,
                         offset: int, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        selected = sorted((item for item in self.messages if item.scene == scene and after <= item.time < before),
                          key=lambda item: item.time)
        return selected[offset:offset + limit]

    def emit_event(self, plugin: str, scene: str, text: str) -> None:
        self._active(plugin, scene)
        PluginStore(self.runtime.store).add_plugin_event(scene, plugin, 'event', self.redact(plugin, text))

    async def generate(self, plugin, scene, prompt, role, system):
        if not self.model_enabled:
            raise RuntimeError('Local plugin testing does not provide a model; pass explicit models to PluginTest')
        return await super().generate(plugin, scene, prompt, role, system)

    async def memory(self, plugin, scene, arguments):
        raise RuntimeError('Local plugin testing does not provide a memory service')

    async def delegate(self, plugin, scene, requester, goal, deliverable, context, materials):
        raise RuntimeError('Local plugin testing does not provide a task worker')


class PluginTest:
    """Use ``async with PluginTest(package, config=..., scenes=...)`` in author tests.

    Copies source to a temporary installation, calls actual start/stop, and uses
    the normal matching, handlers, tools, scene permissions and on-disk KV.
    ``deliveries`` contain simulated sends. Network calls made directly by the
    plugin still execute; this harness does not sandbox plugin Python code.
    """

    def __init__(self, package: Path, *, config: dict | None = None,
                 scenes: Sequence[str] = ('onebot:group:80001',), owners: Sequence[str] = (),
                 now: Callable[[], float] = time.time, models: dict | None = None,
                 data: Path | None = None, data_version: int | None = None):
        """``data`` is copied in as the plugin's existing data directory; ``data_version`` records the
        version it was written with (data without one counts as 1). A higher manifest ``data_version``
        then runs ``migrate_data`` before ``start``, as on a real upgrade."""
        self.now, self.models = now, models
        self.data, self.data_version = data, data_version
        self.package = Path(package)
        self.manifest = parse_manifest(self.package / 'plugin.toml')
        self.manifest.require_compatible()
        self.values = {} if config is None else config
        self.scenes = tuple(validate_scene(scene) for scene in scenes)
        self.manifest.values_model(self.scenes).model_validate(self.values)
        self.owners = list(owners)
        self.deliveries: list[Delivery] = []
        self.host: _LocalHost | None = None
        self.temporary = None

    async def __aenter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lenbot-plugin-test-')
        root = Path(self.temporary.name).resolve()
        plugins = root / 'plugins'
        shutil.copytree(self.package, plugins / self.manifest.name,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv'))
        if self.data is not None:
            shutil.copytree(self.data, root / 'plugin-data' / self.manifest.name)
            if self.data_version is not None:
                write_version(root / 'plugin-data' / self.manifest.name, self.data_version)
        config = HostConfig.model_validate({
            'bot_id': 'onebot:90001', 'owners': self.owners, 'mode': 'isolated-multi',
            'timezone': 'Asia/Shanghai', 'database': root / 'messages.db',
            'delivery': 'simulated', 'onebot': None, 'compaction': {'input_tokens': 2000},
            'models': self.models if self.models is not None else {'providers': {'local': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                              'api_key': 'synthetic'}},
                       'roles': {'mind': {'provider': 'local', 'model': 'local', 'context_window_tokens': 8192}}},
            'plugins': {'paths': [plugins], 'data_directory': root / 'plugin-data', self.manifest.name: self.values},
            'scenes': {scene: {'persona': root / 'unused-role', 'plugins': [self.manifest.name]}
                       for scene in self.scenes},
        })
        try:
            self.host = _LocalHost(config, self.deliveries, now=self.now, model_enabled=self.models is not None)
            await self.host.start()
            record = self.host.plugins[self.manifest.name]
            if record.status != 'running':
                raise RuntimeError(record.error)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *exception):
        try:
            if self.host is not None:
                await self.host.close()
        finally:
            if self.host is not None:
                self.host.runtime.store.db.close()
            self.temporary.cleanup()

    def add_message(self, text: str, *, scene: str = 'onebot:group:80001', sender: str = 'onebot:70001',
                    message_id: str | None = None, timestamp: float | None = None) -> ChatMessage:
        """Store a synthetic received message and return its actual ID for source calls."""
        scene = validate_scene(scene)
        HostConfig.valid_bot_id(sender)
        identifier = uuid4().hex if message_id is None else message_id
        message = ChatMessage(id=identifier, platform=scene.split(':', 1)[0], bot_id=self.host.bot_id,
                              scene=scene, platform_message_id=identifier, sender=Sender(sender, 'Test user', None, None),
                              time=self.host.now() if timestamp is None else timestamp,
                              segments=[Segment('text', {'text': text})], reply_to=None,
                              mentions_bot=False, is_self=sender == self.host.bot_id, send_status='received')
        self.host.messages.append(message)
        self.host.runtime.store.enqueue(message, {}, message.time)
        return message

    def preview_tools(self, scene: str = 'onebot:group:80001') -> list[dict]:
        """Discovery, full schema, shared instructions and availability in this scene."""
        return self.host.tool_previews(scene)

    async def wait_tasks(self, name_prefix: str) -> None:
        """Wait for work explicitly named by the author (exclude perpetual tasks)."""
        tasks = [task for task in self.host.tasks
                 if task.get_name().startswith(f'{self.manifest.name}:{name_prefix}')]
        record = self.host.plugins[self.manifest.name]
        previous_error = record.errors[-1] if record.errors else None
        await asyncio.gather(*tasks)
        if record.errors and record.errors[-1] is not previous_error:
            raise RuntimeError(record.errors[-1]['error'])

    async def message(self, text: str, *, scene: str = 'onebot:group:80001', sender: str = 'onebot:70001') -> bool:
        """Return whether a real handler consumed this synthetic plain-text message."""
        message = self.add_message(text, scene=scene, sender=sender)
        matched = self.host.match_message(message, ())
        if matched is None:
            return False
        previous_error = matched.record.errors[-1] if matched.record.errors else None
        tasks_before = set(self.host.tasks)
        self.host.dispatch_message(message, matched)
        await asyncio.gather(*(task for task in self.host.tasks if task not in tasks_before))
        if matched.record.errors and matched.record.errors[-1] is not previous_error:
            raise RuntimeError(matched.record.errors[-1]['error'])
        return True

    async def tool(self, name: str, arguments: dict, *, scene: str = 'onebot:group:80001') -> str:
        """Call the actual registered tool with normal schema and scene checks."""
        tools = {item.name: item for item in self.host.tools_for(scene)}
        return await tools[name].call(scene, arguments)

    def events(self, scene: str = 'onebot:group:80001') -> list[dict]:
        return PluginStore(self.host.runtime.store).plugin_events(scene)

    def state(self) -> dict:
        return self.host.state()
