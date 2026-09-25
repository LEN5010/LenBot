"""The only social cognition entry: raw conversation, native tools, one terminal."""
from __future__ import annotations

import asyncio
import json
import copy
import time
from dataclasses import replace

from len_bot.cognition.agent_loop import AgentLoop, CommitConflict, FreshInputConflict, TerminalArgumentError, final_step_message, execution_budget_message, _error_text
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.proposals import ProposalLedger, TOOLS
from len_bot.tools.retrieval import ObservationPage, RetrievalToolkit
from len_bot.tools.results import ToolResult
from len_bot.plugins.models import PluginCallContext
from len_bot.cognition.models import ConversationResume
from len_bot.cognition.providers import ModelProfile
from len_bot.cognition.request_record import _LocatedPluginToolDefinition, _RecordedToolDefinition
from len_bot.cognition.budget import AgentBudget, count_remaining, terminal_seconds_reserve, window_deadline
from len_bot.plugins.agent import PluginExecution


class SocialCognitionCore:
    def __init__(self,runtime):
        self.runtime=runtime

    async def run(self,session,events,through_rowid,episode_id,source_event_ids,observe=None,commit=None,trace=None,input_prepared=None, *, requester_qq_uid, recent_event_ids: frozenset[str], publish=None, resume:ConversationResume|None=None,
                  mailbox=None, plugin_call=None, plugin_request=None, save_segment=None):
        runtime=self.runtime
        config=runtime.config.model_copy(deep=True)
        if plugin_request:
            config=config.model_copy(update={'conversation_max_steps':plugin_request.max_steps,
                'conversation_max_tool_calls':plugin_request.max_tool_calls,
                'conversation_context_tokens':plugin_request.context_tokens,
                'conversation_output_tokens':plugin_request.output_tokens})
        if resume:
            config=config.model_copy(update={'conversation_max_steps':resume.model_calls_limit,
                'conversation_max_tool_calls':resume.tool_calls_limit,'conversation_context_tokens':resume.context_tokens,
                'conversation_output_tokens':resume.output_tokens,
                # The window a waiting turn is measured against is the one it
                # was granted — including "no window at all" — not whichever
                # value the root configuration happens to carry by the time
                # the reply arrives.  A turn that waited does not get a
                # re-derived window out of a policy edit, and a window the
                # operator left unlimited stays unlimited on resume.
                'conversation_window_seconds':resume.elapsed_seconds_limit})
        role=plugin_request.model_role if plugin_request else 'conversation'
        binding=(runtime.provider_registry.resolve_profile(resume.model_profile,role) if resume
                 else runtime.provider_registry.resolve(role))
        started=time.monotonic()
        initial_models=resume.model_calls_used if resume else 0
        initial_tools=resume.tool_calls_used if resume else 0
        audit=trace if trace is not None else {}
        audit.update({'mode':'live','path':'conversation','model':binding.model,'provider_id':binding.provider_id,
                      'reasoning_effort':binding.reasoning_effort,
                      'budget_snapshot':{'model_calls_limit':config.conversation_max_steps,
                                         'tool_calls_limit':config.conversation_max_tool_calls,
                                         'elapsed_seconds_limit':config.conversation_window_seconds,
                                         'resumed_elapsed_seconds':resume.elapsed_seconds if resume else 0}})
        context=ConversationContext(runtime,session,through_rowid)
        segment_id=session.conversation_segment.id if session.conversation_segment else None
        if save_segment is not None and session.conversation_segment is not None:
            context.refs.result_aliases=dict(session.conversation_segment.result_aliases)
            context.refs.job_aliases=dict(session.conversation_segment.job_aliases)
            context.refs.memory_aliases=dict(session.conversation_segment.memory_aliases)
            context.refs.task_aliases=dict(session.conversation_segment.task_aliases)
            context.refs.loop_aliases=dict(session.conversation_segment.loop_aliases)
        context.supports_segment_vision = binding.supports_vision
        context.config=config
        context.input_budget=config.conversation_context_tokens-config.conversation_output_tokens
        context.add_current_sources(events,source_event_ids)
        ledger=ProposalLedger(context, episode_id)
        if mailbox:
            ledger.checkpoint_index=mailbox.next_checkpoint
            ledger.messages_committed=mailbox.messages_committed
        execution=plugin_call.execution if plugin_call else PluginExecution(mailbox, audit)
        execution.context, execution.ledger = context, ledger
        execution.model_slot_owned = True
        # A conversation window is absolute: it starts at this run's first
        # model call and closes at one instant.  A resumed wait carries that
        # instant itself, so the time spent waiting for the reply counts
        # against the same window rather than only the time spent running; a
        # record from before the instant was stored falls back to the
        # accumulated running seconds, the only fact it carries.  The last
        # call and the terminal keep a slice of the window so the run still
        # submits its result instead of being cut off mid-thought.
        if resume and resume.deadline_at is not None:
            restored_deadline = time.monotonic() + (resume.deadline_at - runtime.clock())
        else:
            restored_deadline = window_deadline(config.conversation_window_seconds,
                                                resume.elapsed_seconds if resume else 0.0)
        execution.budget = execution.budget or AgentBudget(config.conversation_max_steps,
            config.conversation_max_tool_calls, initial_models, initial_tools,
            deadline=restored_deadline,
            terminal_seconds_reserve=terminal_seconds_reserve(config.conversation_window_seconds),
            terminal_token_reserve=config.conversation_output_tokens)
        if plugin_call:
            ledger.plugin_source_ids.update(mailbox.plugin_source_ids)
            context.plugin_source_ids=mailbox.plugin_source_ids
        if resume:
            ledger.checkpoint_index=resume.next_checkpoint
            ledger.messages_committed=resume.messages_committed
            ledger._next_handle=resume.next_proposal_handle
            ledger.continuing_sources.update(resume.source_event_ids)
        audit.update(model_calls_used=initial_models,tool_calls_used=initial_tools)
        last_decision=None
        refresh_after_commit=False

        def plugin_context(tool_call_id=None):
            if plugin_call:
                return replace(plugin_call, now=runtime.clock(), cutoff_rowid=context.refs.cutoff,
                    episode_id=episode_id,ledger=ledger,tool_call_id=tool_call_id,execution=execution)
            return PluginCallContext(scene_id=session.scene_id, requester_qq_uid=requester_qq_uid,
                now=runtime.clock(), cutoff_rowid=context.refs.cutoff, episode_id=episode_id,
                job_id=None, role='conversation', ledger=ledger,
                requester_qq_uids=tuple(sorted(context.requester_qq_uids)), tool_call_id=tool_call_id,
                source_event_id=source_event_ids[0] if source_event_ids else None,
                event=context.event_records.get(source_event_ids[0]) if source_event_ids else None,
                execution=execution)

        context.capabilities=lambda: runtime.plugin_host.capability_facts(plugin_context(),
            allowed_tool_names=set(plugin_request.tool_names) if plugin_request else None)
        context._delegable_hint = any(runtime.scene_policy.delegable_work_allowed(session.scene_id, requester)
                                      for requester in (sorted(context.requester_qq_uids) or [None]))
        hooks = runtime.plugin_host.run_hooks(plugin_context, audit)

        plugin_proposals = runtime.plugin_host.proposal_tool_names()
        toolkit=RetrievalToolkit(runtime.event_store,[session.scene_id],session.scene_id,
            memory_store=runtime.memory_store,plugin_host=runtime.plugin_host,bot_qq=config.bot_qq,
            media_service=runtime.media_service,context=context,on_observation=runtime.commit_tool_observation,
            config=config, call_context=plugin_context)
        execution.toolkit=toolkit
        if plugin_request:
            toolkit.discovery_tool_names=frozenset(plugin_request.tool_names)
            toolkit.discovered_tools.update({name:None for name in plugin_request.tool_names})
            available={item['function']['name'] for item in toolkit.get_tool_definitions()
                +runtime.plugin_host.get_tool_definitions(plugin_context(),kind='proposal')} | set(TOOLS)
            if set(plugin_request.tool_names)-available:
                raise ValueError('Plugin Agent requested tools unavailable to this scene and entry')
        pending_exchange=None
        pending_presentations=[]
        # Native groups of the saved segment return only under the same model
        # binding: provider continuation fields belong to the model that made
        # them. The Actor decides the segment identity at the first save.
        previous_segment=session.conversation_segment if save_segment is not None else None
        terminal_name=ledger.terminal_definition()['function']['name']
        exchange_break=None
        exchange_gap=None
        request_cutoff=None
        last_definitions=None
        # Read-tool exhaustion closes reads, not a still-budgeted follow-up or wait.
        # An unlimited count has no remaining number to report; the ledger then
        # keeps offering its full action set and the deadline or the account's
        # own refusal is what ends the run.
        ledger.remaining_model_calls=lambda:count_remaining(config.conversation_max_steps, execution.budget.model_used)
        def definitions():
            available=(toolkit.get_tool_definitions() + ledger.definitions()
                    + runtime.plugin_host.get_tool_definitions(plugin_context(), kind='proposal'))
            return [item for item in available if item['function']['name'] in plugin_request.tool_names] if plugin_request else available
        def terminal_definition():
            return ledger.terminal_definition()
        def request_definitions():
            return [*definitions(),terminal_definition()]
        def next_is_final():
            # Closing changes execution eligibility, not the tool catalog.
            return execution.budget.force_terminal(execution.budget.local_state())
        context_started = time.monotonic()
        try:
            initial_budget,_=execution_budget_message({
                'model_calls_limit':config.conversation_max_steps,'model_calls_used':initial_models,
                'tool_calls_limit':config.conversation_max_tool_calls,'tool_calls_used':initial_tools},'respond')
            messages=await context.build(events,source_event_ids,tool_definitions=request_definitions,
                                         execution_budget=initial_budget, recent_event_ids=recent_event_ids,
                                         terminal_hint=final_step_message('respond') if next_is_final() else None,
                                         plugin_request=plugin_request)
            if save_segment is not None and session.conversation_segment is not None:
                await context.install_segment_result_locators(messages,
                    session.conversation_segment.result_aliases,definitions=request_definitions,toolkit=toolkit)
            if (previous_segment is not None and previous_segment.ordered_items
                    and previous_segment.exchange_gap is None
                    and previous_segment.model_profile == ModelProfile(provider_id=binding.provider_id,
                        model=binding.model,reasoning_effort=binding.reasoning_effort,
                        supports_vision=binding.supports_vision)):
                exchange_break=await context.install_segment_exchanges(messages,previous_segment.ordered_items,
                    toolkit=toolkit,definitions=request_definitions)
            if plugin_request:
                await toolkit.import_results(plugin_request.result_ids)
                for ident in plugin_request.result_ids:
                    context.refs.register_result(ident)
                await context.install_initial_materials(toolkit, messages, plugin_request.result_ids,
                    definitions=request_definitions,
                    can_read_body='read_tool_result' in plugin_request.tool_names,
                    can_read_media='read_media' in plugin_request.tool_names)
            if resume:
                await toolkit.import_results(resume.result_ids)
                for ident in resume.result_ids:context.refs.register_result(ident)
                messages.append({'role':'user','_context_section':'resumed_request','content':json.dumps({
                    'kind':'resumed_request','sources':[context.refs.register_event_locator(ident) for ident in resume.source_event_ids],
                    'result_locators':[context.refs.register_result(ident) for ident in resume.result_ids],
                    'messages_already_committed':resume.messages_committed,'next_checkpoint':resume.next_checkpoint},ensure_ascii=False)})
        finally:
            audit['initial_context_ms'] = round((time.monotonic() - context_started) * 1000, 2)
            audit['context_plan']=copy.deepcopy(context.context_plan)

        async def execute(name,args,*,tool_call_id=None):
            if name in TOOLS:return await ledger.stage(name,args)
            if name in plugin_proposals:
                return await runtime.plugin_host.execute_tool(name, args, plugin_context(tool_call_id))
            return await toolkit.execute_observation(name,args,tool_call_id=tool_call_id)

        async def record_tool_result(call, arguments, result):
            raw=result.result if isinstance(result,ObservationPage) else result
            if isinstance(raw,ToolResult) and raw.status in {'error','unsupported'}:
                page=await toolkit.error_observation(call.name,arguments,result,tool_call_id=call.id)
                context.refs.register_result(page.result.result_id)
                return page
            return result

        async def append_update(trajectory):
            # New input is absorbed only while a further exchange still fits:
            # an account with two or more calls left can read the new messages
            # and still submit.  An unlimited count always can.
            left=count_remaining(config.conversation_max_steps, execution.budget.model_used)
            can_absorb=left is None or left>=2
            update=await observe(provided_ranges=context.confirmed_original_ranges) if observe and can_absorb else None
            if update:
                context.session=update['session']
                context.refs.cutoff=update['through_rowid']
                new_events=update['events']
                context.add_current_sources(new_events,[wake.event_id for wake in context.session.pending_wakes])
                if plugin_call:
                    ledger.plugin_source_ids.update(event.id for event in new_events)
                current_ids=[event.id for event in new_events]
                related=await context.associated_originals(new_events,current_ids)
                if not plugin_request or plugin_request.input_mode=='conversation':
                    image_related=await context.associated_image_originals(new_events,current_ids)
                    related=list({event.id:event for event in [*related,*image_related]}.values())
                by_id={event.id:event for event in [*new_events,*related]}
                provided_ids=list(dict.fromkeys([*current_ids,*(event.id for event in related)]))
                await context.pack_events(trajectory,[by_id[ident] for ident in provided_ids],provided_ids,
                    raw_tokens=config.conversation_recent_tokens)
                if not plugin_request or plugin_request.input_mode=='conversation':
                    context.install_image_discussion(trajectory)
                    await context.install_facts(trajectory)
                    await context.install_preferences(trajectory)
                if ledger.jobs or ledger.tasks:
                    trajectory.append({'role':'developer','_context_section':'proposal_status','content':
                        '以上是新增原话与当前事实。已暂存提案尚未生效；若新增要求使目标或确认失效，'
                        '先撤回旧提案再提出当前版本。没有提交的回执不能用来确认已完成。'})
                audit['interim_batches']=audit.get('interim_batches',0)+1

        async def prepare_tool_results(trajectory, entries):
            nonlocal pending_exchange
            reserved=[final_step_message('respond')] if next_is_final() else []
            receipts,pending_exchange=await context.prepare_tool_results(toolkit,trajectory,entries,
                definitions=request_definitions,reserved=reserved,append_update=append_update)
            return receipts

        async def incorporate():
            nonlocal pending_exchange,refresh_after_commit
            trajectory=context.trajectory
            if pending_exchange is not None:
                trajectory.extend(pending_exchange)
                pending_exchange=None
            else:
                await append_update(trajectory)
            if refresh_after_commit:
                if not plugin_request or plugin_request.input_mode=='conversation':
                    await context.install_facts(trajectory)
                    await context.install_preferences(trajectory)
                refresh_after_commit=False
            context.fit_request(trajectory,request_definitions(),phase='new_input')
            return None

        def tag_exchanges(trajectory):
            # Each native group follows the request that produced it; its
            # scene position is that request's read cutoff.
            for message in trajectory:
                if (message.get('role')=='assistant' and message.get('tool_calls')
                        and '_segment_after_rowid' not in message):
                    message['_segment_after_rowid']=request_cutoff if request_cutoff is not None else context.refs.cutoff

        def material_basis(trajectory,definitions):
            """Each fixed material with its compared value and its version basis."""
            values,materials={},[]
            def add(item,basis,version,value):
                values[item]=value
                materials.append({'item':item,'basis':basis,'version':version})
            for message in trajectory:
                if message.get('role')!='system':
                    continue
                content=message.get('content') or ''
                cursor,uncovered=0,[]
                for component in sorted(message.get('_prompt_components',()),key=lambda item:item.start):
                    uncovered.append(content[cursor:component.start])
                    cursor=component.start+len(component.text)
                    add(f'component:{component.component_id}','source_revision',str(component.revision),
                        content[component.start:cursor])
                uncovered.append(content[cursor:])
                # The identity prefix is rendered from root settings; anything
                # else outside a declared component is plugin entry text.
                if uncovered[0]:
                    add('persona','runtime_config',None,uncovered[0])
                if ''.join(uncovered[1:]):
                    add('plugin_instructions','none',None,''.join(uncovered[1:]))
            for definition in definitions:
                name=definition['function']['name']
                value=json.dumps(dict(definition),ensure_ascii=False,sort_keys=True)
                if isinstance(definition,_LocatedPluginToolDefinition):
                    add(f'tool:{name}','plugin_version',
                        f'{definition.plugin_id}@{definition.plugin_version}/api{definition.api_version}',value)
                elif isinstance(definition,_RecordedToolDefinition):
                    configured=(definition.component_id.startswith('core.retrieval.')
                                and name in toolkit.configured_tool_names)
                    add(f'tool:{name}','runtime_config' if configured else 'source_revision',
                        f'{definition.component_id}#{definition.revision}',value)
                else:
                    add(f'tool:{name}','none',None,value)
            for hook,version in runtime.plugin_host.request_material_hooks(plugin_context()):
                add(f'hook:{hook.plugin_id}/{hook.id}/{hook.phase}','hook_output',f'{hook.plugin_id}@{version}',
                    f'{hook.plugin_id}@{version}')
            return values,materials

        async def save_state(trajectory,definitions):
            nonlocal segment_id,exchange_gap
            window_ids=[message['_source_event_id'] for message in trajectory
                if message.get('_context_section') == 'recent_history' and not message.get('_context_omitted')]
            summary_refs=[dict(ref) for message in trajectory
                if message.get('_context_section') == 'history_summary'
                and not message.get('_context_omitted')
                and '_summary_content' in message
                and message.get('content') == message['_summary_content']
                for ref in message['_summary_refs']]
            exchanges,gap=context.segment_exchanges(trajectory,episode_id=episode_id,
                terminal_name=terminal_name,toolkit=toolkit)
            basis,materials=material_basis(trajectory,definitions)
            # Once a group of this run cannot be kept, none of its groups is.
            exchange_gap=exchange_gap or gap
            segment=await save_segment(episode_id=episode_id,expected_id=segment_id,
                through_rowid=context.refs.cutoff,event_ids=window_ids,
                summary_refs=summary_refs,
                result_aliases=dict(context.refs.results),
                job_aliases={ref:job['id'] for ref,job in context.refs.jobs.items()},
                memory_aliases=dict(context.refs.memories),
                task_aliases=dict(context.refs.tasks),
                loop_aliases=dict(context.refs.loops),
                exchanges=[] if exchange_gap else exchanges,
                carried=0 if exchange_gap else sum('_segment_exchange' in message for message in trajectory),
                exchange_break=exchange_break,exchange_gap=exchange_gap,
                profile=ModelProfile(provider_id=binding.provider_id,model=binding.model,
                    reasoning_effort=binding.reasoning_effort,supports_vision=binding.supports_vision),
                basis=basis,materials=materials)
            segment_id=segment.id
            context.context_plan['segment']={'id':segment.id,'previous_id':segment.previous_id,
                'reason':segment.reason,'through_rowid':segment.through_rowid,
                'window_events':len(segment.event_ids),'summary_batches':len(segment.summary_refs),
                'native_exchanges':len(segment.ordered_items),'exchange_gap':exchange_gap,
                'exchange_break':exchange_break,'material_changes':list(segment.material_changes),
                'unversioned_materials':[item.item for item in segment.materials if item.basis!='source_revision'],
                'continuity':'native_exchanges' if segment.ordered_items else 'source_window_only'}
            audit['context_plan']=copy.deepcopy(context.context_plan)

        async def close_exchanges(trajectory):
            # The loop ends without another request; keep its last complete
            # group. A failed save leaves the earlier checkpoint and is reported
            # beside the run's own result, which it cannot undo or repeat.
            if save_segment is None or last_definitions is None:
                return
            tag_exchanges(trajectory)
            try:
                await save_state(trajectory,last_definitions)
            except Exception as error:
                audit['segment_close']={'status':'failed','error':_error_text(error)}
            else:
                audit['segment_close']={'status':'saved'}

        async def finalize_request(trajectory,definitions):
            nonlocal pending_presentations,request_cutoff,last_definitions
            context.trajectory=trajectory
            tag_exchanges(trajectory)
            # Expiry does not increment the scene's knowledge revision. Refresh
            # the existing local preference projection even without new input.
            if not plugin_request or plugin_request.input_mode=='conversation':
                await context.install_preferences(trajectory)
            await context.refresh_segment_result_locators(trajectory,toolkit)
            await toolkit.invalidate_saved_references(trajectory)
            await toolkit.invalidate_result_references()
            tokens=context.fit_request(trajectory,definitions,phase='before_model')
            for message in trajectory:
                if (not message.get('_context_omitted') and '_segment_result_content' in message
                        and message.get('content') == message['_segment_result_content']):
                    context.refs.results.update(message.get('_segment_result_refs',{}))
            context.reconcile_original_reads(trajectory)
            pending_presentations=toolkit.read_presentations(trajectory)
            sections={}
            empty_schema_tokens=context.request_tokens([],[])
            for message in trajectory:
                section=message.get('_context_section',message.get('role','unknown'))
                sections[section]=sections.get(section,0)+context.request_tokens([message],[])-empty_schema_tokens
            context.context_plan['request']={'input_tokens':tokens,'input_budget_tokens':context.input_budget,
                'section_tokens':sections,'tool_definition_tokens':context.request_tokens([],definitions),
                'current_pixel_assets':sorted(context.loaded_media),'messages':context.request_manifest(trajectory)}
            audit['context_plan']=copy.deepcopy(context.context_plan)
            audit['estimated_context_tokens']=tokens
            audit['input_budget_tokens']=context.input_budget
            audit['output_reserved_tokens']=config.conversation_output_tokens
            audit['read_cutoff']=context.refs.cutoff
            audit['call_signals']=dict(context.call_signals)
            if save_segment is not None:
                await save_state(trajectory,definitions)
            request_cutoff=context.refs.cutoff
            last_definitions=definitions
            return context.model_messages(trajectory, toolkit=toolkit)

        async def checkpoint(stage,payload):
            nonlocal pending_presentations
            if stage=='after_model':
                context.confirm_original_reads()
                if mailbox is not None:
                    from len_bot.scenes.models import OriginalCoverage
                    mailbox.provided_original_ranges = {ident: OriginalCoverage.model_validate(span)
                        for ident, span in context.confirmed_original_ranges.items()}
                if input_prepared:
                    input_prepared(context.provided_event_ids, context.refs.read_events)
                toolkit.adopt_presentations(pending_presentations)
                context.confirm_work_result_reads()
                if mailbox is not None:
                    mailbox.provided_result_ranges=copy.deepcopy(toolkit.presented_ranges)
                    mailbox.provided_work_results=set(context.confirmed_work_results)
                actual=copy.deepcopy(pending_presentations)
                pending_presentations=[]
                step=audit['steps'][-1]
                step['presentations']=actual
                step['context_plan']=copy.deepcopy(context.context_plan)
                step['provided_original_ranges']=copy.deepcopy(context.confirmed_original_ranges)
                audit['tool_presented_ranges']=copy.deepcopy(toolkit.presented_ranges)
                payload={**payload,'presentations':actual,'context_plan':copy.deepcopy(context.context_plan)}
            if runtime.evaluation_hook:
                await runtime.evaluation_hook(stage,payload)

        async def finish(arguments, *, owner_call=None, owner_request=None, owner_binding=None):
            nonlocal last_decision,refresh_after_commit
            owner_call=owner_call or plugin_call
            owner_request=owner_request or plugin_request
            owner_binding=owner_binding or binding
            outcome=await ledger.finish(arguments,result_reads=toolkit.presented_ranges,
                resolve_evidence_refs=toolkit.resolve_evidence_refs)
            if owner_call:
                await runtime.plugin_host.validate_call(owner_call)
                for index,message in enumerate(outcome.message_proposals):
                    if message.covered_source_event_ids:
                        raise TerminalArgumentError(f'messages[{index}].covers: 插件表达绑定原唯一来源，不合并处理普通群聊来源')
                    message.plugin_origin=owner_call.origin
                for proposal in outcome.job_proposals:
                    if proposal.operation=='create' and proposal.plugin_origin is None:proposal.plugin_origin=owner_call.origin
                for proposal in outcome.task_proposals:
                    if proposal.operation=='create':proposal.payload.setdefault('plugin_origin',owner_call.origin.model_dump())
            active_hooks=runtime.plugin_host.run_hooks(lambda:owner_call,audit) if owner_call else hooks
            outcome=await active_hooks.before_commit(outcome)
            await ledger.validate_message_segments(outcome)
            if outcome.next_action!='end' and (commit is None or publish is None):
                raise TerminalArgumentError('分阶段执行必须使用真实提交与发布服务')
            if outcome.next_action=='wait':
                outcome.resume_state=ConversationResume(episode_id=episode_id,runtime_started_at=runtime._started_at,
                    model_profile=ModelProfile(provider_id=owner_binding.provider_id,model=owner_binding.model,
                        reasoning_effort=owner_binding.reasoning_effort, supports_vision=owner_binding.supports_vision),
                    model_calls_limit=config.conversation_max_steps,tool_calls_limit=config.conversation_max_tool_calls,
                    model_calls_used=execution.budget.model_used,tool_calls_used=execution.budget.tool_used,
                    context_tokens=config.conversation_context_tokens,output_tokens=config.conversation_output_tokens,
                    elapsed_seconds=(resume.elapsed_seconds if resume else 0)+time.monotonic()-started,
                    elapsed_seconds_limit=config.conversation_window_seconds,
                    deadline_at=(runtime.clock()+execution.budget.deadline-time.monotonic()
                                 if execution.budget.deadline is not None else None),
                    messages_committed=ledger.messages_committed+len(outcome.message_proposals),
                    next_checkpoint=ledger.checkpoint_index+1,next_proposal_handle=ledger._next_handle,
                    source_event_ids=[source.source_event_id for source in outcome.source_outcomes
                        if any(outcome.message_proposals[index].expect_reply for index in source.message_indices)],
                    result_ids=list(context.refs.results.values()),
                    plugin_origin=owner_call.origin if owner_call else None,plugin_request=owner_request)
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
            audit['call_signals']=dict(context.call_signals)
            if commit:
                commit_started = time.monotonic()
                try:
                    decision=await commit(outcome, read_event_ids=context.refs.read_events)
                finally:
                    timings = audit.setdefault('timings_ms', {})
                    timings['commit'] = round(timings.get('commit', 0) + (time.monotonic()-commit_started)*1000, 2)
                if not decision.accepted:raise CommitConflict(decision.reason)
                last_decision=decision
                context.session=decision.scene_session
                committed=decision.committed_proposal.outcome
                ledger.adopt_commit(committed)
                refresh_after_commit=True
                return committed
            return outcome

        async def after_finish(outcome):
            if last_decision is None:return None
            if publish:
                publication_started = time.monotonic()
                try:
                    await publish(last_decision)
                except BaseException as error:
                    # The commit stands; the model got no receipt, so this
                    # terminal group is incomplete and stays out of the segment.
                    audit['segment_close']={'status':'not_saved','reason':'publication_interrupted'
                        if isinstance(error,asyncio.CancelledError) else 'publication_failed'}
                    raise
                finally:
                    timings = audit.setdefault('timings_ms', {})
                    timings['publication'] = round(timings.get('publication', 0)
                                                   + (time.monotonic()-publication_started)*1000, 2)
            if outcome.next_action=='wait':execution.suspended_outcome=outcome
            return {**last_decision.record(),'checkpoint_index':outcome.checkpoint_index,
                    'next':outcome.next_action,'continue_run':outcome.next_action=='continue',
                    'source_outcomes':[source.model_dump(mode='json') for source in outcome.source_outcomes],
                    'delivery':'请以真实发送回执为准；入队不表示sent，unknown不自动重发'}

        execution.finish, execution.after_finish = finish, after_finish
        try:
            run = AgentLoop(ModelGateway(binding,max_output_tokens=config.conversation_output_tokens,
                call_store=runtime.event_store, scene_id=session.scene_id,
                episode_id=plugin_call.origin.run_id if plugin_call else episode_id,
                purpose=('interest_share' if plugin_call and plugin_call.origin.plugin_id == 'interest_share'
                         else 'plugin_agent' if plugin_call else 'conversation'))).run(
                messages=messages,tool_definitions=definitions,
                execute_tool=execute,terminal=terminal_definition,finish=finish,after_finish=after_finish,proposal_tool_names=set(TOOLS) | plugin_proposals,
                ordered_tool_names=runtime.plugin_host.ordered_tool_names(),
                max_steps=config.conversation_max_steps,max_tool_calls=config.conversation_max_tool_calls,
                observe=incorporate,finalize_request=finalize_request,record_tool_result=record_tool_result,prepare_tool_results=prepare_tool_results,
                checkpoint=checkpoint,closed_exchange=close_exchanges,
                trace=audit,initial_model_calls=initial_models,initial_tool_calls=initial_tools,
                hooks=hooks,budget=execution.budget,external_outcome=lambda:execution.suspended_outcome)
            remaining = execution.budget.deadline_seconds()
            if remaining is None:
                return await run
            async with asyncio.timeout(remaining):
                return await run
        except Exception:
            audit['staged_proposals']=[item.model_dump(mode='json') for item in [*ledger.jobs,*ledger.tasks,*ledger.memories]]
            raise
        finally:
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
            audit['context_plan']=copy.deepcopy(context.context_plan)
