import asyncio
import logging
import signal
import sys
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.adapters.onebot import OneBotAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("len_bot")

async def run_app():
    config = RuntimeConfig()
    runtime = AgentRuntime(config)
    adapter = OneBotAdapter(config, on_event=runtime.receive_event)
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
    await adapter.start()

    banner = f"""
======================================================================
  🤖 Len Bot - Persistent Social Agent Runtime v0.2
======================================================================
  ● Web 管理面板 (Dashboard):  {"http://" + config.dashboard_host + ":" + str(config.dashboard_port) if config.dashboard_enabled else "Disabled"}
  ● 默认管理员凭据:           admin / lenbot123
  ● OneBot v11 反向 WS 接口:  ws://{config.ws_host}:{config.ws_port}
  ● 数据库路径:               {config.db_path}
======================================================================
  Bot 正在持续观察环境、守护时间感与未决事务中... 按 Ctrl+C 优雅退出
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
