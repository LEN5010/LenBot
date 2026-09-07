"""The only social cognition entry: raw conversation, native tools, one terminal."""
from __future__ import annotations

from len_bot.cognition.agent_loop import AgentLoop, CommitConflict, FreshInputConflict
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.proposals import ProposalLedger, TOOLS
from len_bot.tools.retrieval import RetrievalToolkit


class SocialCognitionCore:
    def __init__(self,runtime):
        self.runtime=runtime

    async def run(self,session,events,through_rowid,episode_id,source_event_ids,observe=None,commit=None,trace=None,input_prepared=None):
        runtime=self.runtime
        binding=runtime.provider_registry.resolve('conversation')
        audit=trace if trace is not None else {}
        audit.update({'mode':'live','path':'conversation','model':binding.model,'provider_id':binding.provider_id,
                      'reasoning_effort':binding.reasoning_effort})
        context=ConversationContext(runtime,session,through_rowid)
        ledger=ProposalLedger(context,episode_id)
        toolkit=RetrievalToolkit(runtime.event_store,[session.scene_id],session.scene_id,
            memory_store=runtime.memory_store,plugin_host=runtime.plugin_host,bot_qq=runtime.config.bot_qq,
            media_service=runtime.media_service,context=context,on_observation=runtime.commit_tool_observation)
        pending_images=[]
        def definitions():
            return toolkit.get_tool_definitions()+ledger.definitions()
        def request_definitions():
            return [*definitions(),ledger.terminal_definition()]
        messages=await context.build(events,source_event_ids,tool_definitions=request_definitions)

        async def execute(name,args):
            if name in TOOLS:return await ledger.stage(name,args)
            result=await toolkit.execute_result(name,args)
            pending_images.extend(result.attachments)
            return str(result)

        async def incorporate():
            trajectory=context.trajectory
            if pending_images:
                additions=await context.attachments(pending_images)
                context.check_request([*trajectory,*additions],request_definitions())
                trajectory.extend(additions)
                pending_images.clear()
            can_absorb=runtime.config.conversation_max_steps-audit['model_calls_used']>=1
            update=await observe() if observe and can_absorb else None
            if update:
                context.session=update['session']
                context.refs.cutoff=update['through_rowid']
                new_events=update['events']
                facts=await context.facts_message()
                if facts:
                    context.check_request([*trajectory,facts],request_definitions())
                    trajectory.append(facts)
                await context.pack_events(trajectory,new_events,
                    [event.id for event in new_events if event.metadata.get('attention_reasons')],
                    raw_tokens=runtime.config.conversation_recent_tokens)
                if ledger.jobs or ledger.tasks:
                    trajectory.append({'role':'user','content':
                        '以上是新增原话与当前事实。已暂存提案尚未生效；若新增要求使目标或确认失效，'
                        '先撤回旧提案再提出当前版本。没有提交的回执不能用来确认已完成。'})
                audit['interim_batches']=audit.get('interim_batches',0)+1
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
                execute_tool=execute,terminal=ledger.terminal_definition,finish=finish,proposal_tool_names=set(TOOLS),
                max_steps=runtime.config.conversation_max_steps,max_tool_calls=runtime.config.conversation_max_tool_calls,
                observe=incorporate,prepare_request=prepare_request,checkpoint=getattr(runtime,'evaluation_hook',None),trace=audit)
        except Exception:
            audit['staged_proposals']=[item.model_dump(mode='json') for item in [*ledger.jobs,*ledger.tasks]]
            raise
        finally:
            audit['references']=context.refs.snapshot()
            audit['media_manifest']=list(context.media_manifest)
            audit['read_cutoff']=context.refs.cutoff
