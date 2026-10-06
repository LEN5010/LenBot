"""Public local plugin harness: real lifecycle and KV, captured simulated delivery."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import asyncio
import shutil
import tempfile
from types import SimpleNamespace
from uuid import uuid4

from .config import HostConfig
from .platform.identity import validate_scene
from .platform.messages import ChatMessage, Sender, Segment
from .plugin import Content, Image, Sent, Text
from .plugins.host import PluginHost
from .plugins.manifest import parse_manifest
from .plugins.store import PluginStore
from .storage.store import Store


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
    def __init__(self, config: HostConfig, deliveries: list[Delivery]):
        super().__init__(config, core_tools=set())
        self.deliveries = deliveries
        self.messages: list[ChatMessage] = []
        self.runtime = SimpleNamespace(store=Store(config.database))

    async def send_parts(self, plugin: str, scene: str, parts: Sequence[Content], reply_to: str | None) -> Sent:
        self._active(plugin, scene)
        captured = tuple(Text(self.redact(plugin, item.text)) if isinstance(item, Text) else
                         Image(item.data, self.redact(plugin, item.description)) if isinstance(item, Image) else item
                         for item in parts)
        self.deliveries.append(Delivery(plugin, scene, captured, reply_to))
        return Sent('simulated', 'Captured locally; no platform message was sent')

    def recent_messages(self, plugin: str, scene: str, limit: int) -> list[ChatMessage]:
        self._active(plugin, scene)
        return [item for item in self.messages if item.scene == scene][-limit:]

    def emit_event(self, plugin: str, scene: str, text: str) -> None:
        self._active(plugin, scene)
        PluginStore(self.runtime.store).add_plugin_event(scene, plugin, 'event', self.redact(plugin, text))

    async def generate(self, plugin, scene, prompt, role, system):
        raise RuntimeError('Local plugin testing does not provide a model; run model calls in an explicit test instance')

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
                 scenes: Sequence[str] = ('onebot:group:80001',), owners: Sequence[str] = ()):
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
        config = HostConfig.model_validate({
            'bot_id': 'onebot:90001', 'owners': self.owners, 'mode': 'isolated-multi',
            'timezone': 'Asia/Shanghai', 'database': root / 'messages.db',
            'delivery': 'simulated', 'onebot': None, 'compaction': {'input_tokens': 2000},
            'models': {'providers': {'local': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                              'api_key': 'synthetic'}},
                       'roles': {'mind': {'provider': 'local', 'model': 'local', 'context_window_tokens': 8192}}},
            'plugins': {'paths': [plugins], 'data_directory': root / 'plugin-data', self.manifest.name: self.values},
            'scenes': {scene: {'persona': root / 'unused-role', 'plugins': [self.manifest.name]}
                       for scene in self.scenes},
        })
        try:
            self.host = _LocalHost(config, self.deliveries)
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

    async def message(self, text: str, *, scene: str = 'onebot:group:80001', sender: str = 'onebot:70001') -> bool:
        """Return whether a real handler consumed this synthetic plain-text message."""
        scene = validate_scene(scene)
        HostConfig.valid_bot_id(sender)
        identifier = uuid4().hex
        message = ChatMessage(id=identifier, platform=scene.split(':', 1)[0], bot_id=self.host.bot_id,
                              scene=scene, platform_message_id=identifier, sender=Sender(sender, 'Test user', None, None),
                              time=self.host.now(), segments=[Segment('text', {'text': text})], reply_to=None,
                              mentions_bot=False, is_self=sender == self.host.bot_id, send_status='received')
        self.host.messages.append(message)
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
