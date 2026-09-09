"""Plugin dispatch uses the same observations, model calls, Actor and queue."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace

from pydantic import BaseModel

from len_bot.cognition.agent_loop import AgentLoop
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.call_store import estimate_request
from len_bot.events.models import EventType, PluginOrigin
from len_bot.media.models import MessageSegment
from len_bot.runtime.attention import HUMAN_INPUTS
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.tools.results import ToolResult


@dataclass
class PluginExecution:
    mailbox: EpisodeMailbox
    audit: dict = field(default_factory=dict)
    toolkit: RetrievalToolkit | None = None


async def classify_event(runtime, event, cutoff):
    """Save routing facts before attention; matchers do no HTTP or model work."""
    requester = event.actor_id[5:] if event.event_type in HUMAN_INPUTS and event.actor_id.startswith('user:') and event.actor_id != runtime.bot_actor_id else None
    if event.event_type in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}:
        requester = event.payload.get('requester_qq_uid')
    elif event.event_type in {EventType.AGENT_JOB_FINISHED, EventType.AGENT_JOB_PROGRESS, EventType.TASK_DUE, EventType.TASK_REVIEW}:
        job_id = event.payload.get('job_id') or event.payload.get('task_id')
        job = await runtime.event_store.get_job(job_id, event.scene_id) if job_id else None
        if job:
            requester = job['requester_qq_uid']
        elif event.payload.get('payload'):
            requester = event.payload['payload'].get('requester_qq_uid')
        elif job_id:
            tasks = await runtime.event_store.scene_tasks(event.scene_id)
            task = next((item for item in tasks if item['id'] == job_id), None)
            if task:
                requester = task['payload'].get('requester_qq_uid')
    event.metadata['requester_qq_uid'] = requester
    if event.payload.get('reply_to_message_id') is not None:
        projected = await runtime.event_store.project_reply_context(event.scene_id, [event], through_rowid=cutoff)
        event.metadata['quote_context'] = projected[0].metadata['quote_context']
    routes = runtime.plugin_host.match_event(event, cutoff)
    event.metadata['plugin_routes'] = routes
    consumed = any(route['consume'] for route in routes)
    output = event.payload.get('output_kind', 'chat')
    interaction = 'plugin_handler' if consumed else output if output != 'chat' else 'chat'
    eligible = not consumed and output == 'chat' and runtime.scene_policy.chat_allowed(event.scene_id, requester)
    event.metadata.update(interaction=interaction, plugin_consumed=consumed,
        conversation_excluded=not eligible,
        interaction_reason='plugin_consumed' if consumed else 'chat_eligible' if eligible else 'scene_entry_closed')


def _execution(runtime, event, origin, cutoff):
    actor = runtime.scene_manager._actors[event.scene_id]
    mailbox = EpisodeMailbox(origin.run_id, event.scene_id, actor.session.version, origin_stimulus_id=event.id)
    mailbox.output_kind = 'plugin'
    mailbox.plugin_origin = origin
    mailbox.origin_mode = 'shadow' if runtime.shadow_mode or event.metadata.get('delivery_origin') == 'shadow' else 'live'
    mailbox.source_started_at = event.timestamp
    mailbox.requester_qq_uid = event.metadata.get('requester_qq_uid')
    execution = PluginExecution(mailbox, {'plugin_origin': origin.model_dump(), 'state': 'started'})
    call = runtime.plugin_host.handler_call(event, {'origin': origin.model_dump()}, cutoff, execution=execution)
    execution.toolkit = RetrievalToolkit(runtime.event_store, [event.scene_id], event.scene_id,
        memory_store=runtime.memory_store, plugin_host=runtime.plugin_host, bot_qq=runtime.config.bot_qq,
        media_service=runtime.media_service, on_observation=runtime.commit_tool_observation,
        config=runtime.config, call_context=lambda: replace(call, now=runtime.clock()))
    return call


async def dispatch_handler(runtime, event, route, cutoff):
    origin = PluginOrigin.model_validate(route['origin'])
    call = _execution(runtime, event, origin, cutoff)
    audit = call.execution.audit
    await runtime.event_store.save_trace(kind='plugin_run', scene_id=event.scene_id, ref_id=origin.run_id, payload=audit)
    try:
        await runtime.plugin_host.execute_handler(call)
        audit['state'] = 'completed'
    except asyncio.CancelledError:
        call.execution.mailbox.cancel('Plugin run cancelled')
        audit['state'] = 'interrupted'
        raise
    except Exception as error:
        audit.update(state='failed', error=str(error), error_type=type(error).__name__)
        runtime.plugin_host.record_plugin_error(origin.plugin_id, f'{origin.entry_id}: {error}')
    finally:
        await runtime.event_store.save_trace(kind='plugin_run', scene_id=event.scene_id, ref_id=origin.run_id, payload=audit)


async def validate_plugin_origin(runtime, output, scene_id):
    origin = output.plugin_origin
    if origin is None:
        raise ValueError('This output has no current plugin owner; old uncommitted outputs cannot be resumed')
    plugin = runtime.plugin_host.get_plugin(origin.plugin_id)
    if plugin is None or not plugin.manifest.enabled:
        raise ValueError('The plugin responsible for this output is disabled')
    actor = await runtime.scene_manager.get_or_create_actor(scene_id)
    events = await runtime.event_store.events_by_ids(scene_id, [origin.source_event_id], actor.session.last_observed_event_rowid)
    if len(events) != 1:
        raise ValueError('Plugin output has no real source in this scene')
    call = runtime.plugin_host.handler_call(events[0], {'origin': origin.model_dump()}, actor.session.last_observed_event_rowid)
    mention_all = getattr(output, 'mention_all', False) or any(
        segment.type == 'at_all' for segment in getattr(output, 'segments', ()))
    await runtime.plugin_host.validate_call(call, mention_all=mention_all)


async def invoke_tool(runtime, call, name, arguments):
    await runtime.plugin_host.validate_call(call)
    if call.execution is None:
        raise ValueError('Direct tool invocation requires an active plugin execution')
    page = await call.execution.toolkit.execute_observation(name,
        arguments.model_dump(mode='json') if isinstance(arguments, BaseModel) else arguments)
    result = page.result
    call.execution.audit.setdefault('tool_results', []).append({
        'name': name, 'result_id': result.result_id, 'status': result.status})
    return result


async def submit_message(runtime, call, segments, *, mention_all=False):
    await runtime.plugin_host.validate_call(call, mention_all=mention_all)
    if call.execution is None:
        raise ValueError('Message submission requires an active plugin execution')
    mailbox = call.execution.mailbox
    mailbox.mention_all = mention_all
    actor = await runtime.scene_manager.get_or_create_actor(call.scene_id)
    session = actor.session.model_copy(deep=True)
    outcome = EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason='插件提交表达',
        checkpoint_index=mailbox.next_checkpoint,
        message_proposals=[MessageProposal(segments=[MessageSegment.model_validate(segment) for segment in segments])])
    decision = await actor.commit_turn(outcome, call.cutoff_rowid, [call.source_event_id],
        session.knowledge_revision, mailbox, runtime.runtime_gate)
    if not decision.accepted:
        raise ValueError(decision.reason)
    try:
        await runtime.runtime_gate.publish_committed(decision, mailbox)
    finally:
        call.execution.audit.setdefault('commits', []).append(decision.record())
    return decision


async def run_agent(runtime, call, *, instructions: str, input_observations: list[ToolResult],
                    tool_names: tuple[str, ...], model_role: str, output_model: type[BaseModel],
                    max_steps: int, max_tool_calls: int, context_tokens: int, output_tokens: int,
                    include_identity: bool = False, output_mode: str = 'result_only'):
    """Run a plugin-selected result contract through the existing executor."""
    await runtime.plugin_host.validate_call(call)
    if output_mode != 'result_only':
        raise ValueError('This entry requires an explicit result_only contract')
    if call.execution is None:
        raise ValueError('Agent invocation requires an active plugin execution')
    binding = runtime.provider_registry.resolve(model_role)
    toolkit = call.execution.toolkit
    available = {item['function']['name']: item for item in toolkit.get_tool_definitions()}
    if set(tool_names) - set(available):
        raise ValueError('Plugin Agent requested tools unavailable to this scene and entry')
    if output_tokens >= context_tokens:
        raise ValueError('Plugin Agent output reservation must be smaller than its context')
    if include_identity:
        instructions = f'你是{runtime.config.identity_name}。{runtime.config.identity_persona}\n{runtime.config.identity_core}\n' + instructions
    messages = [{'role': 'system', 'content': instructions}]
    for observed in input_observations:
        messages.append({'role': 'user', 'content': json.dumps({'kind': 'plugin_material',
            'observation': observed.model_dump(mode='json', exclude_none=True)}, ensure_ascii=False)})
    terminal = {'type': 'function', 'function': {'name': 'return_result',
        'description': '返回本次结果给调用插件；不会自动发送消息。', 'parameters': output_model.model_json_schema()}}
    audit = {'plugin_origin': call.origin.model_dump(), 'output_mode': output_mode}
    call.execution.audit.setdefault('agents', []).append(audit)

    async def prepare(trajectory, definitions):
        await runtime.plugin_host.validate_call(call)
        if estimate_request(trajectory, definitions)['input_tokens'] > context_tokens-output_tokens:
            raise ValueError('Plugin Agent request exceeds its configured input capacity')
        return trajectory

    async def finish(arguments):
        return output_model.model_validate(arguments, strict=True)

    async def execute(name, arguments, *, tool_call_id=None):
        if name not in tool_names:
            raise ValueError('Plugin Agent tool is outside its declared input contract')
        return await toolkit.execute_result(name, arguments, tool_call_id=tool_call_id)

    result = None
    try:
        async with runtime._cognition_semaphore:
            result = await AgentLoop(ModelGateway(binding, max_output_tokens=output_tokens,
                call_store=runtime.event_store, scene_id=call.scene_id, episode_id=call.origin.run_id,
                purpose='plugin_agent')).run(messages=messages,
                    tool_definitions=lambda: [item for item in toolkit.get_tool_definitions()
                        if item['function']['name'] in tool_names], execute_tool=execute,
                    terminal=terminal, finish=finish, max_steps=max_steps, max_tool_calls=max_tool_calls,
                    prepare_request=prepare, trace=audit)
        return result
    finally:
        await runtime.event_store.set_model_call_disposition(call.origin.run_id, 'plugin_result' if result is not None else 'rejected')
