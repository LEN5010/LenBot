"""Plugin dispatch uses the same observations, model calls, Actor and queue."""
from __future__ import annotations

import asyncio
import json
import copy
from dataclasses import replace
from contextlib import asynccontextmanager

from pydantic import BaseModel

from len_bot.cognition.agent_loop import AgentLoop, execution_budget_message
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.call_store import estimate_request
from len_bot.cognition.budget import AgentBudget
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.models import ConversationResume
from len_bot.cognition.proposals import TOOLS
from len_bot.plugins.agent import PluginAgentRequest, PluginExecution
from len_bot.events.models import EventType, PluginOrigin
from len_bot.media.models import MessageSegment
from len_bot.runtime.attention import HUMAN_INPUTS
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.tools.results import ToolResult


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
    mailbox.plugin_source_ids.add(event.id)
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
    toolkit=copy.copy(call.execution.toolkit)
    toolkit.call_context=lambda:call
    if not toolkit.is_read_only(name):
        raise ValueError('invoke_tool accepts read tools; proposal tools use the active ledger')
    values=arguments.model_dump(mode='json') if isinstance(arguments, BaseModel) else arguments
    if call.execution.budget:
        await call.execution.budget.take_tool(name,values)
    page = await toolkit.execute_observation(name,values,read_slot_owned=call.read_slot_owned)
    result = page.result
    call.execution.audit.setdefault('tool_results', []).append({
        'name': name, 'result_id': result.result_id, 'status': result.status})
    return result


async def submit_message(runtime, call, segments, *, mention_all=False):
    await runtime.plugin_host.validate_call(call, mention_all=mention_all)
    if call.execution is None:
        raise ValueError('Message submission requires an active plugin execution')
    mailbox = call.execution.mailbox
    if mailbox is None:
        raise ValueError('Background work returns results through its existing delivery; it cannot submit direct messages')
    if call.origin.entry_kind=='tool':
        if runtime.plugin_host.tool_capabilities(call.origin.entry_id)['kind']!='proposal':
            raise ValueError('Message submission requires a proposal tool')
        if call.execution.finish is None:
            raise ValueError('This tool has no active expression ledger')
        context=call.execution.context
        parts=[]
        for raw in segments:
            segment=MessageSegment.model_validate(raw)
            if segment.type=='text':parts.append({'text':segment.text})
            elif segment.type=='image':parts.append({'image':context.refs.register_media(segment.asset_id)})
            elif segment.type=='at':parts.append({'at':context.refs.register_actor('user:'+segment.qq_uid)})
            else:raise ValueError('Tool submission does not grant all-member mentions')
        source=context.refs.register_event_locator(call.source_event_id)
        outcome=await call.execution.finish({'messages':[{'segments':parts,'source':source}],
            'sources':[{'source':source,'status':'replied'}],'next':'end'},owner_call=call)
        return await call.execution.after_finish(outcome)
    mailbox.mention_all = mention_all
    actor = await runtime.scene_manager.get_or_create_actor(call.scene_id)
    session = actor.session.model_copy(deep=True)
    outcome = EpisodeOutcome(disposition=FinalDisposition.ACTION, decision_reason='插件提交表达',
        checkpoint_index=mailbox.next_checkpoint,
        message_proposals=[MessageProposal(segments=[MessageSegment.model_validate(segment) for segment in segments],
            source_event_id=call.source_event_id,requester_qq_uid=call.requester_qq_uid,plugin_origin=call.origin)])
    outcome = await runtime.plugin_host.run_hooks(lambda: call, call.execution.audit).before_commit(outcome)
    decision = await actor.commit_turn(outcome, call.cutoff_rowid, [call.source_event_id],
        session.knowledge_revision, mailbox, runtime.runtime_gate)
    if not decision.accepted:
        raise ValueError(decision.reason)
    try:
        await runtime.runtime_gate.publish_committed(decision, mailbox)
    finally:
        call.execution.audit.setdefault('commits', []).append(decision.record())
    return decision


@asynccontextmanager
async def _model_slot(runtime, execution):
    if execution.model_slot_owned:
        yield
    else:
        async with runtime._cognition_semaphore:
            yield


async def _source_events(runtime, call, request, *, resume_event=None, resume=None):
    ids=list(dict.fromkeys([call.source_event_id, *(resume.source_event_ids if resume else ()),
        *([resume_event.id] if resume_event else ())]))
    if request.input_mode=='conversation':
        events=await runtime.event_store.get_recent_events(call.scene_id,
            limit=runtime.config.conversation_history_limit,through_rowid=call.cutoff_rowid,conversation_only=True)
    else:
        events=[]
    originals=await runtime.event_store.events_by_ids(call.scene_id,ids,call.cutoff_rowid)
    events=sorted({event.id:event for event in [*events,*originals]}.values(),key=lambda event:event.metadata['_rowid'])
    return await runtime.event_store.project_reply_context(call.scene_id,events,through_rowid=call.cutoff_rowid),ids


async def _respond_agent(runtime, call, request, *, resume=None, resume_event=None):
    execution=call.execution
    actor=await runtime.scene_manager.get_or_create_actor(call.scene_id)
    mailbox=execution.mailbox
    events,ids=await _source_events(runtime,call,request,resume=resume,resume_event=resume_event)
    mailbox.plugin_source_ids.update(ids)
    if resume:
        if resume.runtime_started_at!=runtime._started_at:
            raise ValueError('Plugin wait belongs to an earlier process; it cannot be restarted automatically')
        mailbox.episode_id=resume.episode_id
        mailbox.messages_committed=resume.messages_committed
        mailbox.next_checkpoint=resume.next_checkpoint
        mailbox.handled_source_ids.update(resume.source_event_ids)
        execution.budget=AgentBudget(resume.model_calls_limit,resume.tool_calls_limit,
            resume.model_calls_used,resume.tool_calls_used)
    current=actor.session.model_copy(deep=True)
    current.pending_wakes=[wake for wake in current.pending_wakes if wake.event_id in ids]
    observed_cutoff=call.cutoff_rowid

    async def observe():
        nonlocal observed_cutoff
        new=actor.session.model_copy(deep=True)
        related=await runtime.event_store.events_by_ids(call.scene_id,
            [wake.event_id for wake in new.pending_wakes if wake.certain and wake.rowid>observed_cutoff],new.last_observed_event_rowid)
        observed_cutoff=new.last_observed_event_rowid
        originals={event.actor_id for event in events}
        message_ids={str(event.payload['message_id']) for event in events if event.payload.get('message_id') is not None}
        related=[event for event in related if event.actor_id in originals
            or str(event.payload.get('reply_to_message_id')) in message_ids
            or ((event.metadata.get('quote_context') or {}).get('plugin_origin') or {}).get('run_id')==call.origin.run_id]
        if not related:return None
        mailbox.plugin_source_ids.update(event.id for event in related)
        new.pending_wakes=[wake for wake in new.pending_wakes if wake.event_id in mailbox.plugin_source_ids]
        return {'session':new,'events':related,'through_rowid':new.last_observed_event_rowid}

    async def commit(outcome, *, read_event_ids):
        return await actor.commit_turn(outcome,execution.context.refs.cutoff,read_event_ids,
            execution.context.session.knowledge_revision,mailbox,runtime.runtime_gate)

    async def publish(decision):
        try:await runtime.runtime_gate.publish_committed(decision,mailbox)
        finally:execution.audit.setdefault('commits',[]).append(decision.record())

    return await runtime.social_core.run(current,events,call.cutoff_rowid,mailbox.episode_id,ids,
        observe=observe,commit=commit,publish=publish,trace=execution.audit,
        requester_qq_uid=call.requester_qq_uid,resume=resume,mailbox=mailbox,
        plugin_call=call,plugin_request=request)


async def run_agent(runtime, call, *, input_observations: list[ToolResult], output_model: type[BaseModel] | None = None, **options):
    """A dedicated loop borrows its parent's call account and concurrency slot."""
    await runtime.plugin_host.validate_call(call)
    request=PluginAgentRequest.model_validate(options)
    parent=call.execution
    if parent is None or parent.toolkit is None:
        raise ValueError('Agent invocation requires an active plugin execution')
    if parent.agent_depth:
        raise ValueError('Recursive plugin Agent invocation is not supported')
    if (request.output_mode=='result_only') != (output_model is not None):
        raise ValueError('result_only requires output_model; respond uses the existing message contract')
    if request.output_mode=='respond' and (parent.mailbox is None or call.origin.entry_kind=='tool'
            and runtime.plugin_host.tool_capabilities(call.origin.entry_id)['kind']!='proposal'):
        raise ValueError('respond requires a handler or a proposal tool with an active expression ledger')
    result_ids=[]
    for observed in input_observations:
        if not isinstance(observed,ToolResult):raise TypeError('Agent materials require typed ToolResult observations')
        if not observed.result_id:
            observed=observed.model_copy(update={'plugin_origin':call.origin})
            observed=(await parent.toolkit.store_observation('plugin_material',
                {'plugin_id':call.origin.plugin_id,'source_event_id':call.source_event_id},observed)).result
        result_ids.append(observed.result_id)
    request=request.model_copy(update={'result_ids':tuple(result_ids)})
    parent.budget=parent.budget or AgentBudget(request.max_steps,request.max_tool_calls)
    async with parent.agent_lock:
        async with _model_slot(runtime,parent):
            audit={'plugin_origin':call.origin.model_dump(),'output_mode':request.output_mode,'input_mode':request.input_mode,
                'requested_budget':{'model_calls_limit':request.max_steps,'tool_calls_limit':request.max_tool_calls,
                    'context_tokens':request.context_tokens,'output_tokens':request.output_tokens},
                'model_role':request.model_role,'tool_names':list(request.tool_names)}
            parent.audit.setdefault('agents',[]).append(audit)
            execution=replace(parent,audit=audit,model_slot_owned=True,agent_depth=1)
            current=replace(call,execution=execution)
            result=None
            try:
                if request.output_mode=='respond' and parent.finish is None:
                    result=await _respond_agent(runtime,current,request)
                else:
                    result=await _dedicated_agent(runtime,current,request,output_model,parent)
                return result
            finally:
                await runtime.event_store.set_model_call_disposition(call.origin.run_id,
                    'plugin_result' if result is not None else 'rejected')


async def _dedicated_agent(runtime, call, request, output_model, parent):
    execution=call.execution
    binding=runtime.provider_registry.resolve(request.model_role)
    nested_respond=request.output_mode=='respond'
    actor=await runtime.scene_manager.get_or_create_actor(call.scene_id)
    context=parent.context if nested_respond else ConversationContext(runtime,actor.session.model_copy(deep=True),call.cutoff_rowid)
    previous_input_budget=context.input_budget
    if not nested_respond:context.input_budget=request.context_tokens-request.output_tokens
    instructions=request.instructions
    if request.include_identity:
        instructions=f'你是{runtime.config.identity_name}。{runtime.config.identity_persona}\n{runtime.config.identity_core}\n'+instructions
    messages=[{'role':'system','content':instructions}]
    if request.input_mode!='materials':
        events,ids=await _source_events(runtime,call,request)
        context.add_current_sources(events,ids)
        await context.pack_events(messages,events,ids,raw_tokens=context.input_budget)

    async def observation(event):
        await runtime.commit_tool_observation(event)
        ident=event.payload['result_id']
        await parent.toolkit.import_results([ident])
        if parent.context:parent.context.refs.register_result(ident)
        if parent.toolkit.checkpoint:
            await parent.toolkit.checkpoint('after_tool',{'scene_id':call.scene_id,'name':event.payload.get('tool_name'),
                'result':parent.toolkit.observations[ident].model_dump(mode='json')})

    toolkit=copy.copy(parent.toolkit) if nested_respond else RetrievalToolkit(runtime.event_store,[call.scene_id],call.scene_id,
        memory_store=runtime.memory_store,plugin_host=runtime.plugin_host,bot_qq=runtime.config.bot_qq,
        media_service=runtime.media_service,context=context,on_observation=observation,
        config=runtime.config,call_context=lambda tool_call_id=None:replace(call,tool_call_id=tool_call_id))
    if nested_respond:toolkit.call_context=lambda:call
    execution.toolkit=toolkit
    toolkit.discovered_tools.update({name:None for name in request.tool_names})
    await toolkit.import_results(request.result_ids)
    for ident in request.result_ids:
        context.refs.register_result(ident)
        messages.append(toolkit.material_message(ident))

    def definitions():
        available=toolkit.get_tool_definitions()
        if nested_respond:available+=parent.ledger.definitions()+runtime.plugin_host.get_tool_definitions(call,kind='proposal')
        return [item for item in available if item['function']['name'] in request.tool_names]

    if set(request.tool_names)-{item['function']['name'] for item in definitions()}:
        raise ValueError('Plugin Agent requested tools unavailable to this scene and entry')
    if nested_respond:
        terminal=parent.ledger.terminal_definition
        messages.append({'role':'developer','content':'本次使用respond提交表达；来源M、对象U和图片I沿已读资料解析。'
            '暂存操作必须按真实引用处理，全部阶段共用父运行的消息和调用额度。'+json.dumps({
                'staged_proposals':{ref:{'kind':kind,'proposal':proposal.model_dump(mode='json') if isinstance(proposal,BaseModel) else proposal}
                    for ref,(kind,proposal) in parent.ledger.staged.items()},
                'messages_committed':parent.ledger.messages_committed},ensure_ascii=False)})
        async def finish(arguments):
            return await parent.finish(arguments,owner_call=call,owner_request=request,owner_binding=binding)
        async def after_finish(outcome):
            receipt=await parent.after_finish(outcome)
            if outcome.next_action=='wait':parent.suspended_outcome=outcome
            return receipt
    else:
        terminal={'type':'function','function':{'name':'return_result','description':'返回结果给插件，不发送消息。',
            'parameters':output_model.model_json_schema()}}
        async def finish(arguments):return output_model.model_validate_json(json.dumps(arguments,ensure_ascii=False),strict=True)
        after_finish=None

    async def execute(name,arguments,*,tool_call_id=None):
        if nested_respond and name in TOOLS:return await parent.ledger.stage(name,arguments)
        if nested_respond and name in runtime.plugin_host.proposal_tool_names():
            return await runtime.plugin_host.execute_tool(name,arguments,replace(call,tool_call_id=tool_call_id))
        return await toolkit.execute_result(name,arguments,tool_call_id=tool_call_id)

    async def prepare(trajectory,definitions):
        await runtime.plugin_host.validate_call(call)
        if estimate_request(trajectory,definitions)['input_tokens']>request.context_tokens-request.output_tokens:
            raise ValueError('Plugin Agent request exceeds its configured input capacity')
        pending_presentations[:]=toolkit.read_presentations(trajectory)
        return context.model_messages(trajectory)

    state=await execution.budget.state()
    reserve=1 if parent.model_slot_owned else 0
    steps=min(request.max_steps,state['model_calls_limit']-state['model_calls_used']-reserve)
    if steps<1:raise ValueError('Parent budget has no model call available for this plugin Agent')
    if request.input_mode=='conversation':
        materials=[message for message in messages if message.get('_context_section')=='plugin_material']
        budget_note,_=execution_budget_message(state,'respond' if nested_respond else 'return_result')
        messages=await context.build(events,ids,execution_budget=budget_note,plugin_request=request,
            tool_definitions=lambda:[*definitions(),terminal() if callable(terminal) else terminal])
        messages.extend(materials)
    if not nested_respond:
        messages.append({'role':'developer','content':'本次只通过return_result返回插件结果；不提供respond、工作或提醒提案，不自动发消息。'})
    pending_presentations=[]

    async def checkpoint(stage,payload):
        if stage=='after_model':
            toolkit.adopt_presentations(pending_presentations)
            if execution.record_presentations:
                await execution.record_presentations(pending_presentations)
            execution.audit['steps'][-1]['presentations']=list(pending_presentations)
            execution.audit['references']=context.refs.snapshot()
        if runtime.evaluation_hook:await runtime.evaluation_hook(stage,payload)

    try:
        return await AgentLoop(ModelGateway(binding,max_output_tokens=request.output_tokens,
            call_store=runtime.event_store,scene_id=call.scene_id,episode_id=call.origin.run_id,job_id=call.job_id,purpose='plugin_agent')).run(
                messages=messages,tool_definitions=definitions,execute_tool=execute,terminal=terminal,finish=finish,
                after_finish=after_finish,proposal_tool_names=set(TOOLS)|runtime.plugin_host.proposal_tool_names() if nested_respond else set(),
                max_steps=steps,max_tool_calls=request.max_tool_calls,budget=execution.budget,
                finalize_request=prepare,checkpoint=checkpoint,trace=execution.audit,hooks=runtime.plugin_host.run_hooks(lambda:call,execution.audit))
    finally:
        if nested_respond:context.input_budget=previous_input_budget


async def resume_agent(runtime,event,cutoff):
    packet=event.metadata['conversation_resume']
    resume=ConversationResume.model_validate(packet['state'])
    origin=resume.plugin_origin
    source=(await runtime.event_store.events_by_ids(event.scene_id,[origin.source_event_id],cutoff))[0]
    call=_execution(runtime,source,origin,cutoff)
    call.execution.agent_depth=1
    audit=call.execution.audit
    audit['resumed_from']={'loop_id':packet['loop_id'],'event_id':event.id}
    try:
        await runtime.plugin_host.validate_call(call)
        async with _model_slot(runtime,call.execution):
            await _respond_agent(runtime,call,resume.plugin_request,resume=resume,resume_event=event)
        audit['state']='completed'
    except asyncio.CancelledError:
        audit['state']='interrupted'
        raise
    except Exception as error:
        audit.update(state='failed',error=str(error),error_type=type(error).__name__)
        runtime.plugin_host.record_plugin_error(origin.plugin_id,str(error))
    finally:
        await runtime.event_store.set_model_call_disposition(origin.run_id,
            'plugin_result' if audit['state']=='completed' else 'rejected')
        await runtime.event_store.save_trace(kind='plugin_run',scene_id=event.scene_id,ref_id=origin.run_id,payload=audit)
