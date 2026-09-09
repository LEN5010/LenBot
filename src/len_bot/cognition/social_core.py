"""The only social cognition entry: raw conversation, native tools, one terminal."""
from __future__ import annotations

import json
import copy
import time
from dataclasses import replace

from len_bot.cognition.agent_loop import AgentLoop, CommitConflict, FreshInputConflict, final_step_message, execution_budget_message
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.proposals import ProposalLedger, TOOLS
from len_bot.tools.retrieval import ObservationPage, RetrievalToolkit
from len_bot.tools.results import ToolResult
from len_bot.plugins.models import PluginCallContext
from len_bot.cognition.models import ConversationResume
from len_bot.cognition.providers import ModelProfile
from len_bot.cognition.budget import AgentBudget
from len_bot.plugins.agent import PluginExecution


class SocialCognitionCore:
    def __init__(self,runtime):
        self.runtime=runtime

    async def run(self,session,events,through_rowid,episode_id,source_event_ids,observe=None,commit=None,trace=None,input_prepared=None, *, requester_qq_uid, publish=None, resume:ConversationResume|None=None,
                  mailbox=None, plugin_call=None, plugin_request=None):
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
                'conversation_output_tokens':resume.output_tokens})
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
                                         'tool_calls_limit':config.conversation_max_tool_calls}})
        context=ConversationContext(runtime,session,through_rowid)
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
        execution.budget = execution.budget or AgentBudget(config.conversation_max_steps,
            config.conversation_max_tool_calls, initial_models, initial_tools)
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
                    ledger=ledger, tool_call_id=tool_call_id, execution=execution)
            return PluginCallContext(scene_id=session.scene_id, requester_qq_uid=requester_qq_uid,
                now=runtime.clock(), cutoff_rowid=context.refs.cutoff, episode_id=episode_id,
                job_id=None, role='conversation', ledger=ledger,
                requester_qq_uids=tuple(sorted(context.requester_qq_uids)), tool_call_id=tool_call_id,
                source_event_id=source_event_ids[0] if source_event_ids else None,
                event=context.event_records.get(source_event_ids[0]) if source_event_ids else None,
                execution=execution)

        context.capabilities=lambda: runtime.plugin_host.capability_facts(plugin_context())
        hooks = runtime.plugin_host.run_hooks(plugin_context, audit)

        plugin_proposals = runtime.plugin_host.proposal_tool_names()
        toolkit=RetrievalToolkit(runtime.event_store,[session.scene_id],session.scene_id,
            memory_store=runtime.memory_store,plugin_host=runtime.plugin_host,bot_qq=config.bot_qq,
            media_service=runtime.media_service,context=context,on_observation=runtime.commit_tool_observation,
            config=config, call_context=plugin_context)
        execution.toolkit=toolkit
        if plugin_request:
            toolkit.discovered_tools.update({name:None for name in plugin_request.tool_names})
            available={item['function']['name'] for item in toolkit.get_tool_definitions()
                +runtime.plugin_host.get_tool_definitions(plugin_context(),kind='proposal')} | set(TOOLS)
            if set(plugin_request.tool_names)-available:
                raise ValueError('Plugin Agent requested tools unavailable to this scene and entry')
        pending_exchange=None
        pending_presentations=[]
        ledger.can_continue=lambda: execution.budget.model_used<config.conversation_max_steps and execution.budget.tool_used<config.conversation_max_tool_calls
        def definitions():
            available=(toolkit.get_tool_definitions() + ledger.definitions()
                    + runtime.plugin_host.get_tool_definitions(plugin_context(), kind='proposal'))
            return [item for item in available if item['function']['name'] in plugin_request.tool_names] if plugin_request else available
        def terminal_definition():
            definition=ledger.terminal_definition()
            if next_is_final():definition['function']['parameters']['properties']['next']['enum']=['end']
            return definition
        def request_definitions():
            if next_is_final():return [terminal_definition()]
            return [*definitions(),terminal_definition()]
        def next_is_final():
            return (execution.budget.model_used>=config.conversation_max_steps-1
                    or execution.budget.tool_used>=config.conversation_max_tool_calls)
        try:
            initial_budget,_=execution_budget_message({
                'model_calls_limit':config.conversation_max_steps,'model_calls_used':initial_models,
                'tool_calls_limit':config.conversation_max_tool_calls,'tool_calls_used':initial_tools},'respond')
            messages=await context.build(events,source_event_ids,tool_definitions=request_definitions,
                                         execution_budget=initial_budget,
                                         terminal_hint=final_step_message('respond') if next_is_final() else None,
                                         plugin_request=plugin_request)
            if plugin_request:
                await toolkit.import_results(plugin_request.result_ids)
                for ident in plugin_request.result_ids:
                    context.refs.register_result(ident)
                    messages.append(toolkit.material_message(ident))
            if resume:
                await toolkit.import_results(resume.result_ids)
                for ident in resume.result_ids:context.refs.register_result(ident)
                messages.append({'role':'user','_context_section':'resumed_request','content':json.dumps({
                    'kind':'resumed_request','sources':[context.refs.register_event_locator(ident) for ident in resume.source_event_ids],
                    'result_locators':[context.refs.register_result(ident) for ident in resume.result_ids],
                    'messages_already_committed':resume.messages_committed,'next_checkpoint':resume.next_checkpoint},ensure_ascii=False)})
        except BaseException:
            audit['context_plan']=copy.deepcopy(context.context_plan)
            raise

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
            can_absorb=config.conversation_max_steps-audit['model_calls_used']>=1
            update=await observe() if observe and can_absorb else None
            if update:
                context.session=update['session']
                context.refs.cutoff=update['through_rowid']
                new_events=update['events']
                context.add_current_sources(new_events,[wake.event_id for wake in context.session.pending_wakes])
                if plugin_call:
                    ledger.plugin_source_ids.update(event.id for event in new_events)
                current_ids=[event.id for event in new_events if event.metadata.get('attention_reasons')]
                related=await context.associated_originals(new_events,current_ids)
                by_id={event.id:event for event in [*new_events,*related]}
                provided_ids=list(dict.fromkeys([*current_ids,*(event.id for event in related)]))
                await context.pack_events(trajectory,[by_id[ident] for ident in provided_ids],provided_ids,
                    raw_tokens=config.conversation_recent_tokens)
                if not plugin_request or plugin_request.input_mode=='conversation':
                    await context.install_facts(trajectory)
                    await context.install_preferences(trajectory)
                if ledger.jobs or ledger.tasks:
                    trajectory.append({'role':'developer','_context_section':'proposal_status','content':
                        '以上是新增原话与当前事实。已暂存提案尚未生效；若新增要求使目标或确认失效，'
                        '先撤回旧提案再提出当前版本。没有提交的回执不能用来确认已完成。'})
                audit['interim_batches']=audit.get('interim_batches',0)+1

        async def prepare_tool_results(trajectory, entries):
            nonlocal pending_exchange
            messages=list(trajectory)
            pages=[]
            indexes=[]
            for call,result in entries:
                if isinstance(result,ObservationPage):
                    indexes.append(len(messages))
                    pages.append(result)
                    result=toolkit.observation_locator(result)
                    content=str(result)
                elif isinstance(result, ToolResult):
                    content = result.model_dump_json(exclude_none=True)
                else:content=result if isinstance(result,str) else json.dumps(result,ensure_ascii=False)
                messages.append({'role':'tool','tool_call_id':call.id,'content':content})
            tool_end=len(messages)
            reserved=[final_step_message('respond')] if next_is_final() else []
            messages.extend(reserved)
            await append_update(messages)
            if reserved:del messages[tool_end:tool_end+len(reserved)]
            current_jobs=dict(context.refs.jobs)

            async def render(position,limit):
                page=pages[position]
                result=await toolkit._present(page.name,page.result,page.offset,limit,page.coordinate_unit)
                # Runtime facts follow the tool replies. An archived query must
                # not overwrite the current work version shown by those facts.
                context.refs.jobs.update(current_jobs)
                return result

            context.fit_request(messages,request_definitions(),reserved=reserved,phase='tool_exchange')
            await context.pack_tool_pages(messages,indexes,[page.limit for page in pages],render,
                definitions=request_definitions,reserved=reserved)
            pending_exchange=messages[tool_end:]
            return [message['content'] for message in messages[len(trajectory):tool_end]]

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

        async def finalize_request(trajectory,definitions):
            nonlocal pending_presentations
            context.trajectory=trajectory
            tokens=context.fit_request(trajectory,definitions,phase='before_model')
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
            if input_prepared:
                input_prepared(context.provided_event_ids,context.refs.read_events)
            return context.model_messages(trajectory)

        async def checkpoint(stage,payload):
            nonlocal pending_presentations
            if stage=='after_model':
                toolkit.adopt_presentations(pending_presentations)
                actual=copy.deepcopy(pending_presentations)
                pending_presentations=[]
                step=audit['steps'][-1]
                step['presentations']=actual
                step['context_plan']=copy.deepcopy(context.context_plan)
                audit['tool_presented_ranges']=copy.deepcopy(toolkit.presented_ranges)
                payload={**payload,'presentations':actual,'context_plan':copy.deepcopy(context.context_plan)}
            if runtime.evaluation_hook:
                await runtime.evaluation_hook(stage,payload)

        async def finish(arguments, *, owner_call=None, owner_request=None, owner_binding=None):
            nonlocal last_decision,refresh_after_commit
            owner_call=owner_call or plugin_call
            owner_request=owner_request or plugin_request
            owner_binding=owner_binding or binding
            outcome=await ledger.finish(arguments)
            if owner_call:
                await runtime.plugin_host.validate_call(owner_call)
                for message in outcome.message_proposals:
                    message.plugin_origin=owner_call.origin
            active_hooks=runtime.plugin_host.run_hooks(lambda:owner_call,audit) if owner_call else hooks
            outcome=await active_hooks.before_commit(outcome)
            await ledger.validate_message_segments(outcome)
            if outcome.next_action!='end' and (commit is None or publish is None):
                from len_bot.cognition.agent_loop import TerminalArgumentError
                raise TerminalArgumentError('分阶段执行必须使用真实提交与发布服务')
            if outcome.next_action=='wait':
                outcome.resume_state=ConversationResume(episode_id=episode_id,runtime_started_at=runtime._started_at,
                    model_profile=ModelProfile(provider_id=owner_binding.provider_id,model=owner_binding.model,reasoning_effort=owner_binding.reasoning_effort),
                    model_calls_limit=config.conversation_max_steps,tool_calls_limit=config.conversation_max_tool_calls,
                    model_calls_used=execution.budget.model_used,tool_calls_used=execution.budget.tool_used,
                    context_tokens=config.conversation_context_tokens,output_tokens=config.conversation_output_tokens,
                    elapsed_seconds=(resume.elapsed_seconds if resume else 0)+time.monotonic()-started,
                    messages_committed=ledger.messages_committed+len(outcome.message_proposals),
                    next_checkpoint=ledger.checkpoint_index+1,next_proposal_handle=ledger._next_handle,
                    source_event_ids=[source.source_event_id for source in outcome.source_outcomes if source.status=='waiting'],
                    result_ids=list(context.refs.results.values()),
                    plugin_origin=owner_call.origin if owner_call else None,plugin_request=owner_request)
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
            audit['call_signals']=dict(context.call_signals)
            if commit:
                decision=await commit(outcome, read_event_ids=context.refs.read_events)
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
            if publish:await publish(last_decision)
            if outcome.next_action=='wait':execution.suspended_outcome=outcome
            return {**last_decision.record(),'checkpoint_index':outcome.checkpoint_index,
                    'next':outcome.next_action,'continue_run':outcome.next_action=='continue',
                    'source_outcomes':[source.model_dump(mode='json') for source in outcome.source_outcomes],
                    'delivery':'请以真实发送回执为准；入队不表示sent，unknown不自动重发'}

        execution.finish, execution.after_finish = finish, after_finish
        try:
            return await AgentLoop(ModelGateway(binding,max_output_tokens=config.conversation_output_tokens,
                call_store=runtime.event_store, scene_id=session.scene_id,
                episode_id=plugin_call.origin.run_id if plugin_call else episode_id,
                purpose='plugin_agent' if plugin_call else 'conversation')).run(
                messages=messages,tool_definitions=definitions,
                execute_tool=execute,terminal=terminal_definition,finish=finish,after_finish=after_finish,proposal_tool_names=set(TOOLS) | plugin_proposals,
                max_steps=config.conversation_max_steps,max_tool_calls=config.conversation_max_tool_calls,
                observe=incorporate,finalize_request=finalize_request,record_tool_result=record_tool_result,prepare_tool_results=prepare_tool_results,
                checkpoint=checkpoint,trace=audit,initial_model_calls=initial_models,initial_tool_calls=initial_tools,
                hooks=hooks,budget=execution.budget,external_outcome=lambda:execution.suspended_outcome)
        except Exception:
            audit['staged_proposals']=[item.model_dump(mode='json') for item in [*ledger.jobs,*ledger.tasks,*ledger.memories]]
            raise
        finally:
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
            audit['context_plan']=copy.deepcopy(context.context_plan)
