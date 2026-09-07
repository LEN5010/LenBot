"""Isolated event replay through the production runtime. Output is always shadow."""
import asyncio
import tempfile

from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.providers import ProviderConfig,RoutingConfig


async def drain(runtime):
    """Quiesce current work without advancing future timers or inventing receipts."""
    for _ in range(30):
        for actor in list(runtime.scene_manager._actors.values()):await actor._queue.join()
        for scene in list(runtime.burst_assembler._buffers):await runtime.burst_assembler.flush_scene(scene)
        running=list(runtime._conversation_tasks.values())
        if running:await asyncio.gather(*running)
        await runtime.scheduler.run_due(runtime.clock())
        work=list(runtime.job_runner._tasks.values())
        if work:await asyncio.gather(*work)
        await runtime.action_queue._queue.join()
        for actor in list(runtime.scene_manager._actors.values()):await actor._queue.join()
        await asyncio.sleep(0)
        if not(runtime._conversation_tasks or runtime._pending_bursts or runtime.burst_assembler._buffers or runtime.job_runner._tasks):return
    raise RuntimeError('Replay did not quiesce')


class ReplayLab:
    def __init__(self,config,*,provider_configuration=None,mock_turn_handler=None):
        self.config=config
        self.provider_configuration=provider_configuration
        self.mock_turn_handler=mock_turn_handler

    async def run(self,events):
        with tempfile.TemporaryDirectory(prefix='lenbot-replay-') as directory:
            config=self.config.model_copy(update={'db_path':directory+'/replay.db','dashboard_enabled':False,
                'message_pacing':False})
            runtime=AgentRuntime(config,mock_turn_handler=self.mock_turn_handler)
            await runtime.start()
            try:
                if self.provider_configuration:
                    data=self.provider_configuration
                    await runtime.provider_registry.apply_update([ProviderConfig.model_validate(p) for p in data['providers']],
                        RoutingConfig.model_validate(data['routing']))
                for source in events:
                    event=source.model_copy(deep=True)
                    event.metadata.pop('_rowid',None)
                    event.metadata['replay_input']=True
                    await runtime.receive_event(event)
                    await drain(runtime)
                return {'mode':'mock' if self.mock_turn_handler else 'live_model','delivery_mode':'shadow',
                    'traces':await runtime.event_store.query_traces(limit=1000),
                    'would_send':list(runtime.shadow_would_send_log),
                    'sessions':{key:actor.session.model_dump(mode='json') for key,actor in runtime.scene_manager._actors.items()}}
            finally:await runtime.stop()
