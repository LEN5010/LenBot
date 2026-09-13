"""Plugin dispatch uses the same observations, model calls, Actor and queue."""
from __future__ import annotations

import asyncio
import json
import copy
from dataclasses import replace
from contextlib import asynccontextmanager

from pydantic import BaseModel, ValidationError

from len_bot.cognition.agent_loop import AgentLoop, FreshInputConflict, TerminalArgumentError, execution_budget_message
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal, SourceOutcome
from len_bot.cognition.jobs import JobResult
from len_bot.cognition.budget import AgentBudget, count_remaining, tightest
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.models import ConversationResume
from len_bot.cognition.providers import ModelProfile
from len_bot.cognition.proposals import TOOLS
from len_bot.plugins.agent import PluginAgentRequest, PluginExecution, RESULT_ONLY_NOTICE, result_definition
from len_bot.events.models import EventType, PluginOrigin
from len_bot.media.models import MessageSegment
from len_bot.runtime.attention import HUMAN_INPUTS
from len_bot.tools.retrieval import ObservationPage, RetrievalToolkit
from len_bot.tools.results import ToolResult


async def classify_event(runtime, event, cutoff):
    """Save routing facts before attention; matchers do no HTTP or model work."""
    requester = event.actor_id[5:] if event.event_type in HUMAN_INPUTS and event.actor_id.startswith('user:') and event.actor_id != runtime.bot_actor_id else None
    work_issue=None
    prepared_work=False
    if event.event_type in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}:
        requester = event.payload.get('requester_qq_uid')
        if event.metadata.get('associated_open_loop') and event.payload.get('plugin_origin'):
            issue=runtime.plugin_host.origin_issue(event.payload['plugin_origin'],event.scene_id)
            if issue:event.metadata['associated_open_loop']['status']='review_required'
    elif event.event_type in {EventType.AGENT_JOB_FINISHED, EventType.AGENT_JOB_PROGRESS, EventType.TASK_DUE, EventType.TASK_REVIEW}:
        job_id = event.payload.get('job_id') or event.payload.get('task_id')
        job = await runtime.event_store.get_job(job_id, event.scene_id) if job_id else None
        if job:
            requester = job['requester_qq_uid']
            work_issue=runtime.plugin_host.work_issue(job)
            prepared_work=(event.event_type in {EventType.AGENT_JOB_FINISHED,EventType.TASK_REVIEW}
                and bool((job['result'] or {}).get('delivery')))
            if prepared_work:
                event.metadata['prepared_work_delivery']={
                    'job_id':job['id'],'job_revision':job['revision'],'plugin_origin':job['plugin_origin']}
        elif event.payload.get('payload'):
            requester = event.payload['payload'].get('requester_qq_uid')
            if event.payload['payload'].get('plugin_origin'):
                work_issue=runtime.plugin_host.origin_issue(event.payload['payload']['plugin_origin'],event.scene_id)
        elif job_id:
            tasks = await runtime.event_store.scene_tasks(event.scene_id)
            task = next((item for item in tasks if item['id'] == job_id), None)
            if task:
                requester = task['payload'].get('requester_qq_uid')
                if task['payload'].get('plugin_origin'):
                    work_issue=runtime.plugin_host.origin_issue(task['payload']['plugin_origin'],event.scene_id)
    event.metadata['requester_qq_uid'] = requester
    if work_issue:event.metadata['plugin_work_issue']=work_issue
    if event.payload.get('reply_to_message_id') is not None:
        projected = await runtime.event_store.project_reply_context(event.scene_id, [event], through_rowid=cutoff)
        event.metadata['quote_context'] = projected[0].metadata['quote_context']
    routes = runtime.plugin_host.match_event(event, cutoff)
    event.metadata['plugin_routes'] = routes
    consumed = any(route['consume'] for route in routes)
    output = event.payload.get('output_kind', 'chat')
    interaction = 'plugin_handler' if consumed else output if output != 'chat' else 'chat'
    eligible = not consumed and not work_issue and not prepared_work and output == 'chat' and runtime.scene_policy.chat_allowed(event.scene_id, requester)
    event.metadata.update(interaction=interaction, plugin_consumed=consumed,
        conversation_excluded=not eligible,
        interaction_reason='plugin_work_unavailable' if work_issue else 'prepared_work_delivery' if prepared_work else 'plugin_consumed' if consumed else 'chat_eligible' if eligible else 'scene_entry_closed')


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
    origin = output if isinstance(output,PluginOrigin) else output.plugin_origin
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
    if getattr(output,'requester_qq_uid',None) is not None:
        call=replace(call,requester_qq_uid=output.requester_qq_uid)
    mention_all = getattr(output, 'mention_all', False) or any(
        segment.type == 'at_all' for segment in getattr(output, 'segments', ()))
    await runtime.plugin_host.validate_call(call, mention_all=mention_all)


async def invoke_tool(runtime, call, name, arguments):
    await runtime.plugin_host.validate_call(call)
    if call.execution is None:
        raise ValueError('Direct tool invocation requires an active plugin execution')
    toolkit=copy.copy(call.execution.toolkit)
    toolkit.call_context=lambda:replace(call,now=runtime.clock())
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
            elif segment.type in {'image', 'video', 'audio'}:parts.append({segment.type:context.refs.register_media(segment.asset_id)})
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


async def deliver_work_result(runtime,event):
    """Submit an already prepared work artifact through the original delivery."""
    delivery=event.metadata['prepared_work_delivery']
    job=await runtime.event_store.get_job(delivery['job_id'],event.scene_id)
    if not job or job['revision']!=delivery['job_revision'] or job['status']!='result_ready' or job['delivery_action_id']:
        return
    result=JobResult.model_validate(job['result'])
    if result.delivery is None:return
    origin=PluginOrigin.model_validate(job['plugin_origin'])
    actor=await runtime.scene_manager.get_or_create_actor(event.scene_id)
    cutoff=actor.session.last_observed_event_rowid
    read_ids=list(dict.fromkeys([origin.source_event_id,job['request_source_event_id'],event.id]))
    sources=await runtime.event_store.events_by_ids(event.scene_id,read_ids,cutoff)
    source=next((item for item in sources if item.id==origin.source_event_id),None)
    if source is None:raise ValueError('Prepared work delivery lost its real plugin source')
    call=replace(_execution(runtime,source,origin,cutoff),requester_qq_uid=job['requester_qq_uid'],
        job_id=job['id'],work_operation=job['work_operation'])
    mailbox=call.execution.mailbox
    mailbox.episode_id=f'work-delivery:{job["id"]}:{job["revision"]}'
    mailbox.origin_stimulus_id=event.id
    mailbox.requester_qq_uid=job['requester_qq_uid']
    mailbox.origin_mode='shadow' if runtime.shadow_mode or job['origin_mode']=='shadow' else 'live'
    mailbox.plugin_source_ids.update(read_ids)
    audit=call.execution.audit
    audit.update(job_id=job['id'],job_revision=job['revision'],artifact_result_id=result.delivery.result_id)
    decision=None
    try:
        issue=runtime.plugin_host.work_issue(job)
        if issue:raise ValueError(issue)
        if origin.scene_entry!='handler' and not runtime.scene_policy.chat_allowed(event.scene_id,job['requester_qq_uid']):
            raise ValueError('The original requester no longer has chat eligibility')
        await runtime.plugin_host.validate_call(call)
        outcome=EpisodeOutcome(disposition=FinalDisposition.ACTION,decision_reason='交付插件已经保存的工作成果',
            message_proposals=[MessageProposal(segments=result.delivery.segments,
                source_event_id=job['request_source_event_id'],requester_qq_uid=job['requester_qq_uid'],
                job_id=job['id'],job_revision=job['revision'],fulfils_task_id=job['id'],plugin_origin=origin)],
            source_outcomes=[SourceOutcome(source_event_id=event.id,status='replied',message_indices=[0])])
        outcome=await runtime.plugin_host.run_hooks(lambda:call,audit).before_commit(outcome)
        decision=await actor.commit_turn(outcome,cutoff,read_ids,actor.session.knowledge_revision,mailbox,runtime.runtime_gate)
        if not decision.accepted:raise ValueError(decision.reason)
        await runtime.runtime_gate.publish_committed(decision,mailbox)
        audit['state']='submitted'
    except FreshInputConflict as error:
        audit.update(state='waiting_for_current_input',reason=str(error))
    except asyncio.CancelledError:
        audit['state']='interrupted'
        raise
    except Exception as error:
        audit.update(state='failed',error=str(error),error_type=type(error).__name__)
        raise
    finally:
        if decision:audit.setdefault('commits',[]).append(decision.record())
        await runtime.event_store.save_trace(kind='plugin_work_delivery',scene_id=event.scene_id,
            ref_id=mailbox.episode_id,payload=audit)


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
    # A dedicated Agent borrows its parent's account.  When the parent has no
    # account of its own — a handler invoked outside a loop — this Agent's own
    # request is all the allowance there is, so it must name at least one
    # counted dimension: with neither count and nothing to borrow, the run
    # would have no stopping condition of its own.  Inside a parent account an
    # unlimited dimension is fine, because that account's deadline, token
    # allowance and counts still stop the run.
    if parent.budget is None and request.max_steps is None and request.max_tool_calls is None:
        raise ValueError('Plugin Agent invoked outside a loop needs a model-call or tool-call limit; '
                         'without a borrowed account there is no stopping condition')
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
            projection=None
            if request.output_mode=='respond' and parent.finish is not None:
                # Share the Ledger and reference identities, but give the child
                # its own visible window; the parent's next request keeps its
                # original tool set, required sources and actual image window.
                projection={name:getattr(parent.context,name) for name in (
                    'input_budget','tool_definitions','trajectory','required_originals','provided_event_ids',
                    'attached','loaded_media','media_manifest','_facts','context_plan','text_tokens')}
                parent.context.attached=set()
                parent.context.loaded_media=set()
                parent.context.media_manifest=[]
                parent.context.provided_event_ids=set()
                parent.context._facts={}
                parent.context.context_plan={'omitted':[]}
            result=None
            try:
                if request.output_mode=='respond' and parent.finish is None:
                    result=await _respond_agent(runtime,current,request)
                else:
                    result=await _dedicated_agent(runtime,current,request,output_model,parent)
                return result
            finally:
                if projection is not None:
                    for name,value in projection.items():setattr(parent.context,name,value)


async def _dedicated_agent(runtime, call, request, output_model, parent):
    execution=call.execution
    if call.job_id and request.model_role=='work':
        job=await runtime.event_store.get_job(call.job_id,call.scene_id)
        if not job or not job['model_binding']:
            raise ValueError('Plugin work Agent requires its existing job model binding')
        binding=runtime.provider_registry.resolve_profile(ModelProfile.model_validate(job['model_binding']),role='work')
    else:
        binding=runtime.provider_registry.resolve(request.model_role)
    nested_respond=request.output_mode=='respond'
    actor=await runtime.scene_manager.get_or_create_actor(call.scene_id)
    context=parent.context if nested_respond else ConversationContext(runtime,actor.session.model_copy(deep=True),call.cutoff_rowid)
    context.input_budget=request.context_tokens-request.output_tokens
    instructions=request.instructions
    if request.include_identity:
        instructions=f'你是{runtime.config.identity_name}。{runtime.config.identity_persona}\n{runtime.config.identity_core}\n'+instructions
    messages=[{'role':'system','content':instructions}]
    if request.input_mode!='materials':
        events,ids=await _source_events(runtime,call,request)
        context.add_current_sources(events,ids)

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
        terminal=result_definition(output_model)
        async def finish(arguments):
            try:
                return output_model.model_validate_json(json.dumps(arguments,ensure_ascii=False),strict=True)
            except ValidationError as error:
                raise TerminalArgumentError('插件结果字段不符合声明的结构') from error
        after_finish=None

    async def execute(name,arguments,*,tool_call_id=None):
        if nested_respond and name in TOOLS:return await parent.ledger.stage(name,arguments)
        if nested_respond and name in runtime.plugin_host.proposal_tool_names():
            return await runtime.plugin_host.execute_tool(name,arguments,replace(call,tool_call_id=tool_call_id))
        return await toolkit.execute_observation(name,arguments,tool_call_id=tool_call_id)

    async def record_tool_result(tool_call,arguments,result):
        raw=result.result if isinstance(result,ObservationPage) else result
        if isinstance(raw,ToolResult) and raw.status in {'error','unsupported'}:
            page=await toolkit.error_observation(tool_call.name,arguments,result,tool_call_id=tool_call.id)
            context.refs.register_result(page.result.result_id)
            return page
        return result

    pending_additions=[]
    async def prepare_tool_results(trajectory,entries):
        receipts,additions=await context.prepare_tool_results(toolkit,trajectory,entries,
            definitions=context.tool_definitions)
        pending_additions.extend(additions)
        return receipts

    async def observe():
        additions=list(pending_additions)
        pending_additions.clear()
        return additions

    async def prepare(trajectory,definitions):
        await runtime.plugin_host.validate_call(call)
        context.trajectory=trajectory
        tokens=context.fit_request(trajectory,definitions,phase='plugin_agent')
        context.context_plan['request']={'input_tokens':tokens,'input_budget_tokens':context.input_budget,
            'current_pixel_assets':sorted(context.loaded_media),'messages':context.request_manifest(trajectory)}
        pending_presentations[:]=toolkit.read_presentations(trajectory)
        return context.model_messages(trajectory)

    state=await execution.budget.state()
    # One model call is held back when this Agent runs inside a tool call: the
    # parent still has to submit the terminal for that same call after the
    # child returns.  Either dimension may be unlimited; `tightest` takes the
    # smallest bound that actually exists and reports None when none does, so
    # an unlimited count never becomes a subtraction against `None`.  When no
    # bound exists at all, this Agent's own account is what refuses — the same
    # deadline and token allowance that stop its parent.
    reserve=1 if parent.model_slot_owned and call.tool_call_id is not None else 0
    steps=tightest(request.max_steps, count_remaining(state.get('model_calls_limit'),state['model_calls_used']))
    if steps is not None:
        steps=max(0, steps-reserve)
    if steps is not None and steps<1:
        raise ValueError('Parent budget has no model call available for this plugin Agent')
    context.tool_definitions=lambda:[*definitions(),terminal() if callable(terminal) else terminal]
    if request.input_mode=='conversation' or nested_respond:
        prepared=messages[1:]
        budget_note,_=execution_budget_message(state,'respond' if nested_respond else 'return_result')
        messages=await context.build(events,ids,execution_budget=budget_note,plugin_request=request,
            tool_definitions=context.tool_definitions)
        messages.extend(prepared)
    elif request.input_mode=='source':
        await context.pack_events(messages,events,ids,raw_tokens=context.input_budget)
    await context.install_initial_materials(toolkit, messages, request.result_ids,
        definitions=context.tool_definitions,
        can_read_body='read_tool_result' in request.tool_names,
        can_read_media='read_media' in request.tool_names)
    if not nested_respond:
        messages.append({'role':'developer','content':RESULT_ONLY_NOTICE})
    pending_presentations=[]

    async def checkpoint(stage,payload):
        if stage=='after_model':
            toolkit.adopt_presentations(pending_presentations)
            if execution.record_presentations:
                await execution.record_presentations(pending_presentations)
            execution.audit['steps'][-1]['presentations']=list(pending_presentations)
            execution.audit['steps'][-1]['context_plan']=copy.deepcopy(context.context_plan)
            execution.audit['references']=context.refs.snapshot()
        if runtime.evaluation_hook:await runtime.evaluation_hook(stage,payload)

    return await AgentLoop(ModelGateway(binding,max_output_tokens=request.output_tokens,
        call_store=runtime.event_store,scene_id=call.scene_id,episode_id=call.origin.run_id,job_id=call.job_id,purpose='plugin_agent')).run(
            messages=messages,tool_definitions=definitions,execute_tool=execute,terminal=terminal,finish=finish,
            after_finish=after_finish,proposal_tool_names=set(TOOLS)|runtime.plugin_host.proposal_tool_names() if nested_respond else set(),
            max_steps=steps,max_tool_calls=request.max_tool_calls,budget=execution.budget,
            finalize_request=prepare,checkpoint=checkpoint,trace=execution.audit,
            prepare_tool_results=prepare_tool_results,record_tool_result=record_tool_result,observe=observe,
            hooks=runtime.plugin_host.run_hooks(lambda:call,execution.audit))


async def resume_agent(runtime,event,cutoff):
    packet=event.metadata['conversation_resume']
    resume=ConversationResume.model_validate(packet['state'])
    origin=resume.plugin_origin
    audit={'plugin_origin':origin.model_dump(),'state':'started',
        'resumed_from':{'loop_id':packet['loop_id'],'event_id':event.id}}
    try:
        sources=await runtime.event_store.events_by_ids(event.scene_id,[origin.source_event_id],cutoff)
        if len(sources)!=1:raise ValueError('Plugin wait has no stored source event')
        call=_execution(runtime,sources[0],origin,cutoff)
        call.execution.agent_depth=1
        call.execution.audit=audit
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
        await runtime.event_store.save_trace(kind='plugin_run',scene_id=event.scene_id,ref_id=origin.run_id,payload=audit)
