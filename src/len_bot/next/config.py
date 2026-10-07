"""Root configuration composition and loading from the sole instance JSON file."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .work.egress_policy import NetworkSettings
from .browser.client import AccountBrowserSettings
from .models.asr import AudioSettings
from .storage.files import sync_directory
from .runtime.identity import IdentitySettings, combine_identities
from .models.limits import ResourceLimits
from .configuration.mcp import MCPService, SERVICE_NAME
from .memory.service import MemorySettings
from .models.client import ModelSettings
from .runtime.logs import LoggingSettings
from .runtime.retention import RetentionSettings
from .configuration.tasks import WorkerSettings
from .tools.web_search import WebSearchSettings
from .configuration.types import STRICT, _valid_timezone, _valid_scene
from .configuration.onebot import OneBotSettings
from .configuration.models import Models
from .configuration.chat import (
    Compaction,
    TextDelivery,
    WebReadSettings,
    ImageSettings,
    ScheduleSettings,
    ScenePersona,
    SceneSettings,
)
from .configuration.plugin import PluginSettings, PluginCatalogSettings
from .configuration.maintenance import (
    PanelSettings,
    TaskArchiveSettings,
    EvaluationSettings,
    ReplayClockSettings,
)

CONFIG_VERSION = 3


class SharedConfig(BaseModel):
    model_config = STRICT
    config_version: Literal[3] = 3
    @property
    def _instance_root(self) -> Path | None:
        # Loader location is not a runtime setting and is not serialized or compared.
        return self.__dict__.get('_source_root')

    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    limits: ResourceLimits = Field(default_factory=ResourceLimits)
    retention: RetentionSettings | None = None
    max_model_requests: int = Field(default=4, gt=0, strict=True)
    bot_id: str
    owners: list[str] = Field(default_factory=list)
    permissions: IdentitySettings = Field(default_factory=IdentitySettings)
    timezone: str
    database: Path
    onebot: OneBotSettings | None = None
    delivery: Literal["simulated", "onebot"] = "simulated"
    max_steps: int = Field(default=8, gt=0)
    turn_timeout_seconds: float = Field(default=90.0, gt=0, allow_inf_nan=False)
    compaction: Compaction = Field(default_factory=Compaction)
    text_delivery: TextDelivery = Field(default_factory=TextDelivery)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    web_read: WebReadSettings | None = None
    web_search: WebSearchSettings | None = None
    memory: MemorySettings | None = None
    worker: WorkerSettings | None = None
    images: ImageSettings = Field(default_factory=ImageSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    task_archive: TaskArchiveSettings | None = None
    models: Models


    @model_validator(mode="after")
    def memory_provider_exists(self) -> SharedConfig:
        if self.memory is not None and self.memory.local.embedding is not None:
            provider = self.memory.local.embedding.provider
            if provider not in self.models.providers:
                raise ValueError(f"memory.local.embedding.provider references unknown provider {provider!r}")
            if self.models.providers[provider].api not in {"openai-chat", "openai-embeddings"}:
                raise ValueError("memory embedding requires an openai-chat or openai-embeddings provider")
        if (self.memory is not None and self.memory.ingest is not None
                and self.models.roles.memory is None):
            raise ValueError("local memory ingest requires explicit models.roles.memory")
        if (self.memory is not None and self.memory.summaries
                and self.models.roles.memory is None):
            raise ValueError("local memory summaries require explicit models.roles.memory")
        if self.worker is not None:
            binding = self.models.roles.worker
            if binding is None:
                raise ValueError("worker requires explicit models.roles.worker")
            if (self.worker.compaction_reserve_tokens + self.worker.compaction_keep_recent_tokens
                    >= binding.context_window_tokens):
                raise ValueError(
                    "worker.compaction_reserve_tokens + worker.compaction_keep_recent_tokens "
                    "must be less than models.roles.worker.context_window_tokens; "
                    "set both limits explicitly for a smaller worker context window"
                )
        return self

    @field_validator("bot_id")
    @classmethod
    def valid_bot_id(cls, value: str) -> str:
        if re.fullmatch(r"[a-z][a-z0-9_-]*:[^:\s/\\]+", value) is None:
            raise ValueError("must be platform:account as text")
        return value

    @field_validator("owners")
    @classmethod
    def valid_owners(cls, values: list[str]) -> list[str]:
        for value in values:
            cls.valid_bot_id(value)
        if len(set(values)) != len(values):
            raise ValueError("owners must contain distinct platform identities")
        return values

    @model_validator(mode="after")
    def owner_is_not_bot(self) -> SharedConfig:
        if self.bot_id in self.owners:
            raise ValueError("owners must not include bot_id")
        return self

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return _valid_timezone(value)

    @model_validator(mode="after")
    def input_and_output_fit_window(self) -> SharedConfig:
        mind = self.models.roles.mind
        if self.compaction.max_output_tokens >= mind.context_window_tokens:
            raise ValueError("compaction.max_output_tokens must be less than mind.context_window_tokens")
        trigger_tokens = self.compaction.input_tokens
        if trigger_tokens + mind.max_output_tokens > mind.context_window_tokens:
            raise ValueError(
                "compaction.input_tokens + models.roles.mind.max_output_tokens must fit "
                "models.roles.mind.context_window_tokens"
            )
        return self

    @model_validator(mode="after")
    def valid_delivery(self) -> SharedConfig:
        if self.delivery == "onebot" and self.onebot is None:
            raise ValueError("delivery=onebot requires onebot transport")
        return self

    @model_validator(mode="after")
    def asr_websocket_capacity(self) -> SharedConfig:
        if (self.delivery != "onebot" or self.models.roles.asr is None
                or self.onebot is None or self.onebot.action_transport != "websocket"):
            return self
        # get_record returns the complete WAV as base64 plus its JSON envelope.
        required = 4 * ((self.audio.max_bytes + 2) // 3) + 64 * 1024
        if "max_frame_bytes" not in self.onebot.model_fields_set:
            self.onebot = self.onebot.model_copy(update={
                "max_frame_bytes": max(self.onebot.max_frame_bytes, required),
            })
        elif self.onebot.max_frame_bytes < required:
            raise ValueError(
                f"ASR WebSocket actions require onebot.max_frame_bytes >= {required} "
                f"for audio.max_bytes={self.audio.max_bytes}; increase max_frame_bytes, "
                "lower audio.max_bytes, or explicitly select HTTP actions with http_url"
            )
        return self

    def model_settings(self, role: Literal["mind", "vision", "memory", "worker", "learner"]) -> ModelSettings:
        binding = getattr(self.models.roles, role)
        if binding is None:
            raise ValueError(f"models.roles.{role} is not configured")
        provider = self.models.providers[binding.provider]
        return ModelSettings.from_binding(provider, binding)


def _check_schedule_identity(bot_id: str, schedules: ScheduleSettings) -> None:
    if (schedules.owner == bot_id or bot_id in schedules.admins
            or bot_id in schedules.whitelist):
        raise ValueError("schedules owner, admins and whitelist must not include bot_id")


class LabConfig(SharedConfig, SceneSettings):
    mode: Literal["isolated"]
    scene: str
    panel: PanelSettings | None = None
    evaluation: EvaluationSettings | None = None
    replay_clock: ReplayClockSettings | None = None

    @field_validator("scene")
    @classmethod
    def valid_scene(cls, value: str) -> str:
        return _valid_scene(value)

    @model_validator(mode="after")
    def bot_is_not_schedule_requester(self) -> LabConfig:
        if (self.replay_web is not None or self.replay_images is not None) and (
                self.onebot is not None or self.panel is not None or self.delivery != 'simulated'):
            raise ValueError('replay materials require isolated stdin, simulated delivery and no panel')
        _check_schedule_identity(self.bot_id, self.schedules)
        if (self.tasks.owner == self.bot_id or self.bot_id in self.tasks.admins
                or self.bot_id in self.tasks.whitelist):
            raise ValueError("tasks owner, admins and whitelist must not include bot_id")
        if self.tasks.enabled or self.worker is not None:
            raise ValueError("worker tasks require the isolated-multi host, not the single-scene lab or replay")
        if self.learning is not None:
            if self.scene.split(":", 2)[1] != "group":
                raise ValueError("learning is only supported for group scenes")
            if (self.learning.extract or self.learning.jargon_extract or self.learning.collect_stickers
                    or self.learning.reply_effects):
                raise ValueError(
                    "learning requires the isolated-multi host for extraction, "
                    "not the single-scene lab or replay"
                )
            if (self.learning.embedding is not None
                    and self.learning.embedding.provider not in self.models.providers):
                raise ValueError("learning.embedding.provider references unknown provider "
                                 f"{self.learning.embedding.provider!r}")
        if self.proactive is not None:
            raise ValueError("proactive requires the isolated-multi host, not the single-scene lab or replay")
        if self.plugins:
            raise ValueError("plugins require the isolated-multi host, not the single-scene lab or replay")
        if self.transcribe_audio:
            raise ValueError("automatic audio transcription requires the isolated-multi host")
        if self.task_archive is not None and self.task_archive.scenes != [self.scene]:
            raise ValueError('task_archive.scenes must contain only the configured scene')
        if self.replay_clock is not None:
            incompatible = [
                field for field, enabled in (
                    ("onebot", self.onebot is not None),
                    ("panel", self.panel is not None),
                    ("web_read", self.web_read is not None and self.replay_web is None),
                    ("web_search", self.web_search is not None and self.replay_web is None),
                    ("memory", self.memory is not None),
                    ("models.roles.vision", self.models.roles.vision is not None and self.replay_images is None),
                    ('task_archive', self.task_archive is not None),
                    ("worker", self.worker is not None),
                    ("delivery", self.delivery != "simulated"),
                ) if enabled
            ]
            if incompatible:
                raise ValueError(
                    "replay_clock requires isolated stdin with delivery=simulated and no external "
                    f"or offline entry settings; incompatible: {', '.join(incompatible)}"
                )
        return self

    def scene_timezone(self, scene: str) -> str:
        if scene != self.scene:
            raise ValueError(f"Scene {scene} is not configured")
        return self.timezone


class HostConfig(SharedConfig):
    mode: Literal["isolated-multi"]
    account_browser: AccountBrowserSettings | None = None
    onebot: OneBotSettings | None = Field(...)
    panel: PanelSettings | None = None
    scenes: dict[str, SceneSettings] = Field(min_length=1)
    plugins: PluginSettings | None = None
    plugin_catalog: PluginCatalogSettings = Field(default_factory=PluginCatalogSettings)
    mcp: dict[str, MCPService] = Field(default_factory=dict)

    @model_validator(mode='after')
    def stdin_host_is_explicitly_simulated(self):
        recorded = [scene for scene, settings in self.scenes.items()
                    if settings.replay_web is not None or settings.replay_images is not None]
        if recorded and self.onebot is not None:
            raise ValueError(f'Per-scene replay materials require the explicit stdin host, not platform input: {recorded!r}')
        if self.onebot is None:
            incompatible = [name for name, enabled in (
                ('delivery', self.delivery != 'simulated'),
            ) if enabled]
            if incompatible:
                raise ValueError(f'host without OneBot requires simulated delivery: {incompatible!r}')
        return self

    @field_validator("mcp")
    @classmethod
    def mcp_names(cls, value: dict[str, MCPService]) -> dict[str, MCPService]:
        if any(SERVICE_NAME.fullmatch(name) is None or "__" in name for name in value):
            raise ValueError("MCP names use 1–24 lowercase letters/digits/underscores, start with a letter, and exclude '__'")
        return value

    @field_validator("scenes")
    @classmethod
    def valid_scenes(cls, scenes: dict[str, SceneSettings]) -> dict[str, SceneSettings]:
        for scene in scenes:
            _valid_scene(scene)
        return scenes

    @model_validator(mode="after")
    def bot_is_not_schedule_requester(self) -> HostConfig:
        for scene, settings in self.scenes.items():
            try:
                _check_schedule_identity(self.bot_id, settings.schedules)
            except ValueError as error:
                raise ValueError(f"scenes.{scene}: {error}") from error
            if (settings.tasks.owner == self.bot_id or self.bot_id in settings.tasks.admins
                    or self.bot_id in settings.tasks.whitelist):
                raise ValueError(f"scenes.{scene}.tasks owner, admins and whitelist must not include bot_id")
            if settings.tasks.enabled and self.worker is None:
                raise ValueError(f"scenes.{scene}.tasks.enabled requires global worker settings")
            if settings.transcribe_audio and self.models.roles.asr is None:
                raise ValueError(f"scenes.{scene}.transcribe_audio requires explicit models.roles.asr")
            if settings.proactive is not None and scene.split(":", 2)[1] != "group":
                raise ValueError(f"scenes.{scene}.proactive is only supported for group scenes")
            if settings.proactive is not None and (settings.learning is None or not settings.learning.reply_effects):
                raise ValueError(f"scenes.{scene}.proactive requires learning.reply_effects to judge actual responses")
            loaded = {} if self.plugins is None else self.plugins.configured
            unknown = [name for name in settings.plugins if name not in loaded]
            if unknown:
                raise ValueError(f"scenes.{scene}.plugins are not configured under root plugins: {unknown!r}")
            if settings.learning is not None:
                if scene.split(":", 2)[1] != "group":
                    raise ValueError(f"scenes.{scene}.learning is only supported for group scenes")
                if ((settings.learning.extract or settings.learning.jargon_extract or settings.learning.reply_effects)
                        and self.models.roles.learner is None):
                    raise ValueError(f"scenes.{scene}.learning requires explicit models.roles.learner")
                if settings.learning.collect_stickers and self.models.roles.vision is None:
                    raise ValueError(f"scenes.{scene}.learning.collect_stickers requires explicit models.roles.vision")
                if (settings.learning.embedding is not None
                        and settings.learning.embedding.provider not in self.models.providers):
                    raise ValueError(f"scenes.{scene}.learning.embedding.provider references unknown provider "
                                     f"{settings.learning.embedding.provider!r}")
        if self.task_archive is not None:
            unknown = set(self.task_archive.scenes) - self.scenes.keys()
            if unknown:
                raise ValueError(f'task_archive.scenes are not configured: {sorted(unknown)!r}')
        return self

    def scene_config(self, scene: str) -> LabConfig:
        if scene not in self.scenes:
            raise ValueError(f"Scene {scene} is not configured")
        # Both typed parts were validated at the single root boundary. Keep
        # parsed local clocks and paths as typed values rather than roundtripping.
        shared = {name: getattr(self, name) for name in SharedConfig.model_fields}
        # Offline settings belong to the original root object and their
        # commands, not to this derived runtime scene view or a saved root file.
        shared['task_archive'] = None
        local = {name: getattr(self.scenes[scene], name) for name in SceneSettings.model_fields}
        shared["timezone"] = self.scene_timezone(scene)
        del local["timezone"]
        shared["permissions"] = combine_identities(self.permissions, local.pop("permissions"))
        config = LabConfig.model_construct(**shared, **local, mode="isolated", scene=scene)
        object.__setattr__(config, '_source_root', self._instance_root)
        return config

    def scene_timezone(self, scene: str) -> str:
        override = self.scenes[scene].timezone
        return self.timezone if override is None else override

    @model_validator(mode="after")
    def configured_mcp_scenes(self) -> HostConfig:
        for name, service in self.mcp.items():
            unknown = set(service.scenes) - self.scenes.keys()
            if unknown:
                raise ValueError(f"mcp.{name}.scenes references unconfigured scenes: {sorted(unknown)}")
            if service.enabled and not service.scenes:
                raise ValueError(f"mcp.{name}: enabled service requires at least one scene")
        return self


def _resolved_path(root: Path, value: object, *, within_root: bool, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a nonempty path string")
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if within_root and not resolved.is_relative_to(root):
        raise ValueError(f"{field} must resolve inside the lab root")
    return resolved


def _read_root(root: Path) -> tuple[Path, dict]:
    root = root.resolve()
    path = root / "lenbot.config.json"
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}") from error
    if not isinstance(source, dict):
        raise ValueError(f"{path}: configuration must be a JSON object")
    return path, source


def _validation_error(path: Path, error: ValidationError, kind: str) -> ValueError:
    details = "; ".join(
        f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
        for item in error.errors(include_input=False)
    )
    return ValueError(f"{path}: invalid {kind} configuration: {details}")


def _resolve_archive_paths(root: Path, source: dict) -> None:
    tasks = source.get('task_archive')
    if isinstance(tasks, dict):
        tasks['destination'] = _resolved_path(root, tasks.get('destination'), within_root=True,
                                               field='task_archive.destination')
        backup_root = root.resolve() / '.backups'
        if tasks['destination'] == backup_root or not tasks['destination'].is_relative_to(backup_root):
            raise ValueError(f'task_archive.destination must be a new directory below root .backups: {tasks["destination"]}')


def _resolve_memory_path(root: Path, source: dict) -> None:
    memory = source.get("memory")
    if isinstance(memory, dict) and isinstance(memory.get("local"), dict):
        memory["local"]["directory"] = _resolved_path(
            root, memory["local"].get("directory"), within_root=True, field="memory.local.directory",
        )


def _resolve_worker_paths(root: Path, source: dict) -> None:
    worker = source.get("worker")
    if not isinstance(worker, dict):
        return
    binary = worker.get("docker_binary")
    if not isinstance(binary, str) or not Path(binary).is_absolute():
        raise ValueError("worker.docker_binary must be an explicit absolute path string")
    worker["docker_binary"] = Path(binary).resolve()
    pool = worker.get('storage_pool')
    if isinstance(pool, dict):
        pool['mount'] = _resolved_path(root, pool.get('mount'), within_root=False, field='worker.storage_pool.mount')
        if pool.get('kind') == 'ext4':
            pool['image'] = _resolved_path(root, pool.get('image'), within_root=False, field='worker.storage_pool.image')
    for name in ("workspace_root", "runtime_root", "delivery_root"):
        worker[name] = _resolved_path(
            root, worker.get(name), within_root=pool is None or name == 'delivery_root', field=f"worker.{name}",
        )
    if worker.get("skills_directory") is not None:
        worker["skills_directory"] = _resolved_path(
            root, worker["skills_directory"], within_root=True, field="worker.skills_directory",
        )


def _load_lab_source(path: Path, source: dict) -> LabConfig:
    root = path.parent
    for name in ('replay_web', 'replay_images'):
        if source.get(name) is not None:
            source[name] = _resolved_path(root, source[name], within_root=True, field=name)
    source["database"] = _resolved_path(root, source.get("database"), within_root=True, field="database")
    source["persona"] = _resolved_path(root, source.get("persona"), within_root=False, field="persona")
    panel = source.get("panel")
    if isinstance(panel, dict) and panel.get("assets_dir") is not None:
        panel["assets_dir"] = _resolved_path(
            root, panel["assets_dir"], within_root=False, field="panel.assets_dir"
        )
    evaluation = source.get("evaluation")
    if isinstance(evaluation, dict):
        evaluation["runs_directory"] = _resolved_path(
            root, evaluation.get("runs_directory", "data/eval/runs"),
            within_root=True, field="evaluation.runs_directory",
        )
        sets = evaluation.get("sets")
        if isinstance(sets, dict):
            for name, location in sets.items():
                sets[name] = _resolved_path(
                    root, location, within_root=False, field=f"evaluation.sets.{name}",
                )
    logs = source.setdefault("logging", {})
    if isinstance(logs, dict):
        logs["directory"] = _resolved_path(root, logs.get("directory", "logs"), within_root=True, field="logging.directory")
    _resolve_archive_paths(root, source)
    _resolve_memory_path(root, source)
    _resolve_worker_paths(root, source)
    try:
        config = LabConfig.model_validate(source)
        object.__setattr__(config, '_source_root', root)
        return config
    except ValidationError as error:
        raise _validation_error(path, error, "lab") from error


def _load_host_source(path: Path, source: dict) -> HostConfig:
    root = path.parent
    source["database"] = _resolved_path(root, source.get("database"), within_root=True, field="database")
    panel = source.get("panel")
    if isinstance(panel, dict) and panel.get("assets_dir") is not None:
        panel["assets_dir"] = _resolved_path(
            root, panel["assets_dir"], within_root=False, field="panel.assets_dir"
        )
    scenes = source.get("scenes")
    if isinstance(scenes, dict):
        for scene, settings in scenes.items():
            if isinstance(settings, dict):
                settings["persona"] = _resolved_path(
                    root, settings.get("persona"), within_root=False,
                    field=f"scenes.{scene}.persona",
                )
                for name in ('replay_web', 'replay_images'):
                    if settings.get(name) is not None:
                        settings[name] = _resolved_path(root, settings[name], within_root=True,
                                                        field=f'scenes.{scene}.{name}')
    logs = source.setdefault("logging", {})
    if isinstance(logs, dict):
        logs["directory"] = _resolved_path(root, logs.get("directory", "logs"), within_root=True, field="logging.directory")
    _resolve_archive_paths(root, source)
    _resolve_memory_path(root, source)
    _resolve_worker_paths(root, source)
    browser = source.get("account_browser")
    if isinstance(browser, dict):
        for field in ("socket", "binary", "home"):
            if isinstance(browser.get(field), str):
                # Keep relative paths relative so the explicit-absolute validator can reject them.
                browser[field] = Path(browser[field])
    plugins = source.get("plugins")
    if isinstance(plugins, dict):
        paths = plugins.get("paths", ["plugins"])
        if not isinstance(paths, list):
            raise ValueError("plugins.paths must be a list of directory path strings")
        plugins["paths"] = [_resolved_path(root, value, within_root=False, field=f"plugins.paths[{index}]")
                            for index, value in enumerate(paths)]
        plugins["data_directory"] = _resolved_path(
            root, plugins.get("data_directory", "plugins/.data"), within_root=True, field="plugins.data_directory",
        )
    services = source.get("mcp")
    if isinstance(services, dict):
        for name, service in services.items():
            if isinstance(service, dict) and isinstance(transport := service.get("transport"), dict):
                if transport.get("type") == "stdio":
                    transport["cwd"] = _resolved_path(root, transport.get("cwd", "."), within_root=False,
                                                       field=f"mcp.{name}.transport.cwd")
    try:
        config = HostConfig.model_validate(source)
        object.__setattr__(config, '_source_root', root)
        if config.onebot is None and config.worker is not None:
            for name in ('workspace_root', 'runtime_root', 'delivery_root'):
                location = getattr(config.worker, name)
                if location == root.resolve():
                    raise ValueError(f'stdin worker.{name} must be below its independent instance root: {location}')
        return config
    except ValidationError as error:
        raise _validation_error(path, error, "host") from error


def load_config(root: Path) -> LabConfig:
    """Load only ``root/lenbot.config.json``; no environment or CLI overlay."""
    path, source = _read_root(root)
    return _load_lab_source(path, source)


def read_scene_persona(root: Path) -> ScenePersona:
    """Read only the three scene-persona values from the validated single-scene root."""
    config = load_config(root)
    return ScenePersona.model_construct(
        persona_aliases=config.persona_aliases,
        relationships=config.relationships,
        behavior_addendum=config.behavior_addendum,
    )


def save_scene_persona(root: Path, changes: ScenePersona) -> None:
    """Replace only scene-persona values in the sole validated root file."""
    path, candidate = _read_root(root)
    candidate["persona_aliases"] = changes.persona_aliases
    candidate["relationships"] = changes.relationships
    candidate["behavior_addendum"] = changes.behavior_addendum
    # Path resolution is only for validation. Preserve the original relative
    # paths and every other raw root field in the file that is written.
    _load_lab_source(path, copy.deepcopy(candidate))

    descriptor, name = tempfile.mkstemp(prefix=".lenbot-config-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(candidate, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def load_host_config(root: Path) -> HostConfig:
    """Load one explicit multi-scene host from its sole root configuration."""
    path, source = _read_root(root)
    return _load_host_source(path, source)


def load_instance_config(root: Path) -> LabConfig | HostConfig:
    """Select the explicitly declared instance format from the one root file."""
    path, source = _read_root(root)
    if source.get("mode") == "isolated":
        return _load_lab_source(path, source)
    if source.get("mode") == "isolated-multi":
        return _load_host_source(path, source)
    raise ValueError(f"{path}: mode must explicitly be isolated or isolated-multi")
