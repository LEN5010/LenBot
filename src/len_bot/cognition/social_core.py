"""The only social cognition entry: raw conversation, native tools, one terminal."""
from __future__ import annotations

import json

from len_bot.cognition.agent_loop import AgentLoop, CommitConflict, FreshInputConflict, final_step_message
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.proposals import ProposalLedger, TOOLS
from len_bot.tools.retrieval import ObservationPage, RetrievalToolkit
from len_bot.tools.results import ToolResult
from len_bot.plugins.models import PluginCallContext


class SocialCognitionCore:
    def __init__(self,runtime):
        self.runtime=runtime

    async def run(self,session,events,through_rowid,episode_id,source_event_ids,observe=None,commit=None,trace=None,input_prepared=None, *, requester_qq_uid):
        runtime=self.runtime
        binding=runtime.provider_registry.resolve('conversation')
        audit=trace if trace is not None else {}
        audit.update({'mode':'live','path':'conversation','model':binding.model,'provider_id':binding.provider_id,
                      'reasoning_effort':binding.reasoning_effort})
        context=ConversationContext(runtime,session,through_rowid)
        ledger=ProposalLedger(context, episode_id, requester_qq_uid)

        def plugin_context():
            return PluginCallContext(scene_id=session.scene_id, requester_qq_uid=requester_qq_uid,
                now=runtime.clock(), cutoff_rowid=context.refs.cutoff, episode_id=episode_id,
                job_id=None, role='conversation', ledger=ledger)

        plugin_proposals = runtime.plugin_host.proposal_tool_names()
        toolkit=RetrievalToolkit(runtime.event_store,[session.scene_id],session.scene_id,
            memory_store=runtime.memory_store,plugin_host=runtime.plugin_host,bot_qq=runtime.config.bot_qq,
            media_service=runtime.media_service,context=context,on_observation=runtime.commit_tool_observation,
            config=runtime.config, call_context=plugin_context)
        pending_exchange=None
        def definitions():
            return (toolkit.get_tool_definitions() + ledger.definitions()
                    + runtime.plugin_host.get_tool_definitions(plugin_context(), kind='proposal'))
        def request_definitions():
            if next_is_final():return [ledger.terminal_definition()]
            return [*definitions(),ledger.terminal_definition()]
        def next_is_final():
            return (audit.get('model_calls_used',0)>=runtime.config.conversation_max_steps-1
                    or audit.get('tool_calls_used',0)>=runtime.config.conversation_max_tool_calls)
        messages=await context.build(events,source_event_ids,tool_definitions=request_definitions)

        async def execute(name,args):
            if name in TOOLS:return await ledger.stage(name,args)
            if name in plugin_proposals:
                return await runtime.plugin_host.execute_tool(name, args, plugin_context())
            return await toolkit.execute_observation(name,args)

        async def append_update(trajectory):
            can_absorb=runtime.config.conversation_max_steps-audit['model_calls_used']>=1
            update=await observe() if observe and can_absorb else None
            if update:
                context.session=update['session']
                context.refs.cutoff=update['through_rowid']
                new_events=update['events']
                facts=await context.facts_message()
                if facts:
                    trajectory.append(facts)
                if ledger.jobs or ledger.tasks:
                    trajectory.append({'role':'user','content':
                        '以上是新增原话与当前事实。已暂存提案尚未生效；若新增要求使目标或确认失效，'
                        '先撤回旧提案再提出当前版本。没有提交的回执不能用来确认已完成。'})
                await context.pack_events(trajectory,new_events,
                    [event.id for event in new_events if event.metadata.get('attention_reasons')],
                    raw_tokens=runtime.config.conversation_recent_tokens)
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
            reserved=[final_step_message('finish_turn')] if next_is_final() else []
            messages.extend(reserved)
            await append_update(messages)
            if reserved:del messages[tool_end:tool_end+len(reserved)]
            current_jobs=dict(context.refs.jobs)

            async def render(position,limit):
                page=pages[position]
                result=await toolkit._present(page.name,page.result,page.offset,limit)
                # Runtime facts follow the tool replies. An archived query must
                # not overwrite the current work version shown by those facts.
                context.refs.jobs.update(current_jobs)
                return result

            await context.pack_tool_pages(messages,indexes,[page.limit for page in pages],render,
                definitions=request_definitions,reserved=reserved)
            pending_exchange=messages[tool_end:]
            return [message['content'] for message in messages[len(trajectory):tool_end]]

        async def incorporate():
            nonlocal pending_exchange
            trajectory=context.trajectory
            if pending_exchange is not None:
                trajectory.extend(pending_exchange)
                pending_exchange=None
            else:
                await append_update(trajectory)
            context.check_request(trajectory,request_definitions())
            return None

        async def prepare_request(trajectory,definitions):
            context.trajectory=trajectory
            tokens=context.check_request(trajectory,definitions)
            audit['estimated_context_tokens']=tokens
            audit['input_budget_tokens']=context.input_budget
            audit['output_reserved_tokens']=runtime.config.conversation_output_tokens
            audit['read_cutoff']=context.refs.cutoff
            audit['call_signals']=dict(context.call_signals)
            if input_prepared:
                input_prepared(context.provided_event_ids,context.refs.read_events)
            return context.model_messages(trajectory)

        async def finish(arguments):
            if context.required_originals - context.refs.read_events:
                raise FreshInputConflict('Required original text remains partially unread; use read_message_range before finishing. Nothing committed.')
            outcome=await ledger.finish(arguments)
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
            audit['call_signals']=dict(context.call_signals)
            if commit:
                decision=await commit(outcome, read_event_ids=context.refs.read_events)
                if not decision.accepted:raise CommitConflict(decision.reason)
            return outcome

        try:
            return await AgentLoop(ModelGateway(binding,max_output_tokens=runtime.config.conversation_output_tokens,
                call_store=runtime.event_store, scene_id=session.scene_id, episode_id=episode_id,
                purpose='conversation')).run(
                messages=messages,tool_definitions=definitions,
                execute_tool=execute,terminal=ledger.terminal_definition,finish=finish,proposal_tool_names=set(TOOLS) | plugin_proposals,
                max_steps=runtime.config.conversation_max_steps,max_tool_calls=runtime.config.conversation_max_tool_calls,
                observe=incorporate,prepare_request=prepare_request,prepare_tool_results=prepare_tool_results,
                checkpoint=getattr(runtime,'evaluation_hook',None),trace=audit)
        except Exception:
            audit['staged_proposals']=[item.model_dump(mode='json') for item in [*ledger.jobs,*ledger.tasks]]
            raise
        finally:
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
