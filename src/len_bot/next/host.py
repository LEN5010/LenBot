"""Run the configured multi-scene host with OneBot or explicit simulated stdin."""

import asyncio
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
import signal
import traceback

import uvicorn

from .config import load_host_config
from .instance_lock import instance_lock
from .operations import host_logging, credentials, redact
from .chat_context import PROMPTS
from .chat_tools import build_tools, tool_catalog
from .host_panel import create_app
from .model import ChatModel
from .model_slots import ModelSlots
from .limits import ModelBudget
from .memory import open_memory
from .memory_ingest import open_memory_ingestor
from .learning import ExpressionLearner
from .jargon import JargonLearner
from .sticker_collection import StickerCollector
from .reply_effects import ReplyEffectTracker
from .sticker_store import StickerStore
from .expression_selection import open_expression_service
from .network import NetworkRuntime
from .persona import load_persona
from .plugin_host import PluginHost
from .mcp_host import MCPHost
from .store import Store
from .tasks import WorkTasks
from .tasks_store import TaskStore
from .skills import load_catalog, select_skills


class HostPanelServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The host coordinates the HTTP server and the OneBot runtime.
        yield


async def run_with_panel(runtime: NetworkRuntime, server: HostPanelServer) -> None:
    def stop() -> None:
        runtime.stop()
        server.should_exit = True

    async def serve() -> None:
        try:
            await server.serve()
        except SystemExit as error:
            # Uvicorn reports bind failures with SystemExit; preserve its cause
            # while allowing the peer runtime to finish its shutdown path.
            raise RuntimeError(f"Panel server exited: {error}") from error

    async def run_runtime() -> None:
        try:
            await runtime.run(manage_signals=False, manual_connection=True)
        except Exception as error:
            # The business lifetime has ended; keep only management available.
            # Closed runners and services are not restarted by a connect button.
            runtime.accepting = False
            runtime.last_runtime_error = redact("".join(traceback.format_exception(error)), runtime.log_secrets)
            runtime._status("failed")
            runtime._emit({"type": "runtime", "status": "failed", "error": runtime.last_runtime_error})

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    tasks = [asyncio.create_task(run_runtime()), asyncio.create_task(serve())]
    errors: list[BaseException] = []
    try:
        try:
            await tasks[1]
        except BaseException as error:
            errors.append(error)
        finally:
            stop()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        errors.extend(result for result in results if isinstance(result, BaseException))
        if errors:
            raise BaseExceptionGroup("Multi-scene host stopped with errors", errors)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def run() -> None:
    root = Path.cwd()
    if not (root / 'lenbot.config.json').exists():
        from .setup import run_setup
        await run_setup(root)
        return
    config = load_host_config(root)
    personas = {path: load_persona(path)
                for path in dict.fromkeys(settings.persona for settings in config.scenes.values())}
    scenes = [(config.scene_config(scene), personas[settings.persona])
              for scene, settings in config.scenes.items()]
    plugins = (None if config.plugins is None else PluginHost(
        config, core_tools={tool["function"]["name"] for tool in tool_catalog(platform=True)}))
    skills = {settings.scene: (
        select_skills((*load_catalog(config.worker.skills_directory, settings.scene,
                                     public_browser=config.worker.public_browser),
                       *(() if plugins is None else plugins.skills_for(settings.scene))), persona.skills)
        if config.worker is not None else ()
    ) for settings, persona in scenes}
    slots = ModelSlots(config.max_model_requests)
    with host_logging(config.logging, credentials(config)), Store(config.database) as store:
        store.recover_plugin_calls()
        if config.onebot is None and (TaskStore(store).containers() or TaskStore(store).browser_in_use()):
            raise ValueError('stdin模拟宿主不能清理原库中残留的容器或账号浏览会话；先在所属原实例明确处理，不使用导入的定位访问外部实例')
        budget = ModelBudget(config, store, None, root=config._instance_root)
        budget.trials_root = root / ".runtime" / "chat-tests"
        slots.admit = budget.check
        sticker_records = StickerStore(store)
        for scene, settings in config.scenes.items():
            if settings.learning is None or not settings.learning.collect_stickers:
                sticker_records.recover(scene)
        if TaskStore(store).browser_in_use() and (config.account_browser is None or config.worker is None):
            raise ValueError('仍有未清理的账号浏览会话；保留原account_browser与worker配置完成清理后再停用')
        if config.worker is None and TaskStore(store).containers():
            raise ValueError("仍有未清理的任务容器；保留原 worker 配置完成清理后再停用任务执行器")
        async with (
            ChatModel(config.model_settings("mind")) as mind,
            ChatModel(config.model_settings("voice")) as voice,
            (ChatModel(config.model_settings("vision")) if config.models.roles.vision is not None
             else nullcontext(None)) as vision,
            (ChatModel(config.model_settings("learner"))
             if any(settings.learning is not None and (settings.learning.extract or settings.learning.jargon_extract
                                                       or settings.learning.reply_effects)
                    for settings in config.scenes.values())
             else nullcontext(None)) as learner_model,
            open_expression_service(config, store, slots=slots) as expression_service,
            open_memory(config, store, active_personas={settings.scene: persona.id for settings, persona in scenes}, slots=slots) as memory,
            open_memory_ingestor(config, store, memory, list(config.scenes), slots=slots) as ingestor,
        ):
            budget.memory = memory
            if expression_service is not None:
                for scene in expression_service.scenes:
                    expression_service.validate(scene)

            def task_update(scene: str) -> None:
                runner = runtime.runners.get(scene)
                if runner is not None:
                    runner.changed.set()
                runtime.notify()

            data_tools = {}
            for settings, persona in scenes:
                selected = [deepcopy(tool["function"]) for tool in build_tools(
                    settings, persona, platform=config.delivery == "onebot",
                ) if tool["function"]["name"] in {"recall_chat", "memory", "transcribe"}]
                for tool in selected:
                    if tool["name"] == "memory":
                        tool["parameters"]["properties"]["action"]["enum"] = memory.actions
                        tool["description"] += "\n" + (
                            PROMPTS / f"next_memory_{memory.settings.backend}.md"
                        ).read_text()
                data_tools[settings.scene] = selected
            tasks = (WorkTasks(config, store, slots, task_update, skills=skills,
                              memory=memory, data_tools=data_tools,
                              skill_permissions={settings.scene: persona.skills
                                                 for settings, persona in scenes},
                              tool_permissions={settings.scene: persona.tools
                                                for settings, persona in scenes})
                     if config.worker is not None else None)
            learning = (None if not any(settings.learning is not None and settings.learning.extract
                                        for settings in config.scenes.values()) else
                        ExpressionLearner(config, store, learner_model, slots=slots,
                                          expression_service=expression_service))
            jargon = (None if not any(settings.learning is not None and settings.learning.jargon_extract
                                      for settings in config.scenes.values()) else
                      JargonLearner(config, store, learner_model, slots=slots))
            sticker_collection = (None if not any(
                settings.learning is not None and settings.learning.collect_stickers
                for settings in config.scenes.values()) else
                StickerCollector(config, store, vision, slots=slots,
                                 recording=lambda scene: runtime.chats[scene].replay_images))
            reply_effects = (None if not any(
                settings.learning is not None and settings.learning.reply_effects
                for settings in config.scenes.values()) else
                ReplyEffectTracker(config, store, learner_model, slots=slots))
            mcp = MCPHost(config.mcp, reserved_tools={tool["function"]["name"] for tool in tool_catalog(platform=True)}
                          | (set() if plugins is None else set(plugins.tool_owner)))
            try:
                await mcp.start()
                if tasks is not None:
                    tasks.mcp = mcp
                runtime = NetworkRuntime(config, scenes, store, mind, voice, vision=vision, slots=slots,
                                         memory=memory, ingestor=ingestor, tasks=tasks, budget=budget, learning=learning, jargon=jargon,
                                         expression_service=expression_service, sticker_collection=sticker_collection,
                                         reply_effects=reply_effects, plugins=plugins, mcp=mcp)
                if config.panel is None:
                    await runtime.run()
                else:
                    app = create_app(config, runtime, root=Path.cwd())
                    server = HostPanelServer(uvicorn.Config(
                        app, host=config.panel.host, port=config.panel.port,
                    ))
                    await run_with_panel(runtime, server)
            finally:
                await mcp.close()



def main() -> None:
    with instance_lock(Path.cwd()):
        asyncio.run(run())


if __name__ == "__main__":
    main()
