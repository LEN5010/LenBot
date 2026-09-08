import asyncio
import logging
import signal
import os
import sys
from len_bot.config_store import ConfigStore
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.adapters.onebot import OneBotAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("len_bot")

async def run_app():
    # The SDK reads ambient headers independently of HTTPX trust_env.
    # This process uses only the operator configuration for provider settings.
    for name in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
                 "OPENAI_WEBHOOK_SECRET", "OPENAI_BASE_URL", "OPENAI_CUSTOM_HEADERS"):
        os.environ.pop(name, None)
    config_store = ConfigStore.load()
    config = config_store.current.runtime
    runtime = AgentRuntime(config, config_store=config_store)
    adapter = OneBotAdapter(
        config,
        on_event=runtime.receive_event,
        on_self_id=runtime.update_bot_identity,
    )
    runtime.action_queue.send_adapter = adapter.send_action
    runtime._onebot_adapter = adapter

    dashboard_server = None
    dashboard_task = None
    if config.dashboard_enabled:
        import uvicorn
        from len_bot.web.app import create_app
        app = create_app(runtime)
        uvi_config = uvicorn.Config(
            app=app,
            host=config.dashboard_host,
            port=config.dashboard_port,
            log_level="warning"
        )
        dashboard_server = uvicorn.Server(uvi_config)
        dashboard_task = asyncio.create_task(dashboard_server.serve())
        logger.info("Len Bot Dashboard running at http://%s:%d", config.dashboard_host, config.dashboard_port)

    await runtime.start()
    adapter.restore_own_message_ids(await runtime.event_store.own_sent_message_ids(f"user:{config.bot_qq}"))
    await adapter.start()

    onebot_link = (
        "主动连接已配置的 WebSocket 服务"
        if config.onebot_connection_mode == "forward_ws"
        else f"等待接入 ws://{config.ws_host}:{config.ws_port}"
    )
    action_transport = "WebSocket" if config.onebot_action_transport == "websocket" else "HTTP"

    banner = f"""
======================================================================
  🤖 Len Bot - Native Conversation and Work Runtime
======================================================================
  ● Web 管理面板 (Dashboard):  {"http://" + config.dashboard_host + ":" + str(config.dashboard_port) if config.dashboard_enabled else "Disabled"}
  ● OneBot v11 消息连接:      {onebot_link}
  ● OneBot v11 发送方式:      {action_transport}
  ● 数据库路径:               {config.db_path}
======================================================================
  Bot 已启动，等待群聊与工作事件。 按 Ctrl+C 优雅退出
======================================================================
"""
    print(banner)

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received, closing...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        if dashboard_server:
            dashboard_server.should_exit = True
            if dashboard_task:
                await dashboard_task
        logger.info("Stopping OneBot adapter...")
        await adapter.stop()
        logger.info("Stopping Agent Runtime...")
        await runtime.stop()
        logger.info("Len Bot stopped cleanly.")

def main() -> None:
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
