"""Native command and announcement entry points use the existing commit/queue."""
from __future__ import annotations

import asyncio
import json

from len_bot.actions.models import ActionItem
from len_bot.cognition.agent_loop import AgentLoop
from len_bot.cognition.gateway import ModelGateway
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.call_store import estimate_request
from len_bot.events.models import EventType
from len_bot.media.models import MessageSegment
from len_bot.runtime.attention import HUMAN_INPUTS


async def classify_event(runtime, event, cutoff):
    """Called inside the scene's writer before attention and persistence."""
    interaction = 'chat'
    requester = event.actor_id[5:] if event.actor_id.startswith('user:') and event.actor_id != runtime.bot_actor_id else None
    if event.event_type in HUMAN_INPUTS:
        calendar = runtime.plugin_host.get_plugin('asoul_calendar')
        command = calendar.match_command(event.raw_text) if calendar and calendar.manifest.enabled else None
        if command:
            interaction = 'calendar_command'
            event.metadata['command_id'] = command
            event.metadata['command_allowed'] = runtime.scene_policy.command_allowed(event.scene_id, command)
        elif event.payload.get('reply_to_message_id') is not None:
            projected = await runtime.event_store.project_reply_context(event.scene_id, [event], through_rowid=cutoff)
            quote = projected[0].metadata.get('quote_context', {})
            if quote.get('interaction') in {'calendar_command', 'calendar_response', 'calendar_comment'}:
                interaction = 'calendar_comment'
                event.metadata['calendar_parent_event_id'] = quote['event_id']
    elif event.event_type in {EventType.LIVE_STARTED, EventType.LIVE_ENDED}:
        interaction = 'announcement'
    elif event.event_type in {EventType.MESSAGE_SENT, EventType.MESSAGE_SEND_FAILED, EventType.ACTION_SHADOWED}:
        kind = event.payload.get('output_kind', 'chat')
        interaction = 'calendar_response' if kind == 'command' else kind
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
    event.metadata['interaction'] = interaction
    eligible = interaction == 'chat' and runtime.scene_policy.chat_allowed(event.scene_id, requester)
    event.metadata['conversation_excluded'] = not eligible
    if not runtime.scene_policy.enabled(event.scene_id):
        event.metadata['interaction_reason'] = 'group_disabled'
    elif interaction != 'chat':
        event.metadata['interaction_reason'] = interaction
    elif not eligible:
        event.metadata['interaction_reason'] = 'chat_closed_for_requester'
    else:
        event.metadata['interaction_reason'] = 'chat_eligible'


async def validate_native_origin(runtime, output: EpisodeMailbox | ActionItem, scene_id):
    """Only a stored deterministic command or source session owns this path."""
    source_id = output.origin_event_id if isinstance(output, ActionItem) else output.origin_stimulus_id
    if not source_id:
        raise ValueError('Native output is missing its real source event')
    actor = runtime.scene_manager._actors.get(scene_id)
    if actor is None:
        raise ValueError('Native output has no scene')
    events = await runtime.event_store.events_by_ids(scene_id, [source_id], actor.session.last_observed_event_rowid)
    if len(events) != 1:
        raise ValueError('Native output source is outside this group')
    source = events[0]
    if output.output_kind == 'command':
        if (source.metadata.get('interaction') != 'calendar_command'
                or source.metadata.get('command_id') != output.command_id
                or not runtime.scene_policy.command_allowed(scene_id, output.command_id)):
            raise ValueError('日程命令已关闭或来源不对应')
    elif output.output_kind == 'announcement':
        if (source.event_type != EventType.LIVE_STARTED or not source.payload.get('notification')
                or source.payload['member'] != output.announcement_member
                or not runtime.scene_policy.announcement_allowed(scene_id, output.announcement_member)):
            raise ValueError('该群未订阅这项开播公告')
        live = runtime.plugin_host.get_plugin('bilibili_live_sensor')
        if live is None or not live.manifest.enabled:
            raise ValueError('直播监测已停用')
        live.validate_session(source.payload['member'], source.payload['room_id'], source.payload['started_at'])
    else:
        raise ValueError('Unknown native output kind')


async def _commit_expression(runtime, event, mailbox, segments):
    actor = await runtime.scene_manager.get_or_create_actor(event.scene_id)
    session = actor.session.model_copy(deep=True)
    outcome = EpisodeOutcome(disposition=FinalDisposition.ACTION,
        decision_reason='原生交互完成', message_proposals=[MessageProposal(segments=segments)])
    decision = await actor.commit_turn(outcome, session.last_observed_event_rowid, [event.id],
        session.knowledge_revision, mailbox, runtime.runtime_gate)
    if not decision.accepted:
        raise ValueError(decision.reason)
    return decision


def _mailbox(runtime, event, kind):
    actor = runtime.scene_manager._actors[event.scene_id]
    mailbox = EpisodeMailbox(f'{kind}:{event.id}', event.scene_id, actor.session.version,
        origin_stimulus_id=event.id)
    mailbox.output_kind = kind
    mailbox.origin_mode = 'shadow' if runtime.shadow_mode or event.metadata.get('delivery_origin') == 'shadow' else 'live'
    mailbox.source_started_at = event.timestamp
    mailbox.requester_qq_uid = event.metadata.get('requester_qq_uid')
    mailbox.command_id = event.metadata.get('command_id')
    mailbox.announcement_member = event.payload.get('member') if kind == 'announcement' else None
    return mailbox


async def handle_calendar_command(runtime, event):
    mailbox = _mailbox(runtime, event, 'command')
    audit = {'command_id': mailbox.command_id, 'source_event_id': event.id, 'episode_id': mailbox.episode_id}
    try:
        await validate_native_origin(runtime, mailbox, event.scene_id)
        plugin = runtime.plugin_host.get_plugin('asoul_calendar')
        result = await plugin.render_command(mailbox.command_id, now=event.timestamp)
        asset = await runtime.media_service.save_generated(result.png, event.scene_id, event.id, '日程命令生成图片')
        decision = await _commit_expression(runtime, event, mailbox, [MessageSegment(type='image', asset_id=asset['id'])])
        audit.update({'state': 'committed', 'action_ids': decision.action_ids,
                      'schedule': result.schedule.model_dump(mode='json'), 'asset_id': asset['id']})
    except asyncio.CancelledError:
        audit.update(state='interrupted')
        raise
    except Exception as error:
        audit.update(state='failed', error=str(error), error_type=type(error).__name__)
    finally:
        await runtime.event_store.save_trace(kind='calendar_command', scene_id=event.scene_id,
            ref_id=event.id, payload=audit)


async def handle_live_announcement(runtime, event):
    mailbox = _mailbox(runtime, event, 'announcement')
    audit = {'source_event_id': event.id, 'member': event.payload['member'],
             'episode_id': mailbox.episode_id, 'state': 'generating'}
    terminal = {'type': 'function', 'function': {'name': 'finish_announcement',
        'description': '提交当前订阅群这一场真实开播的邀请正文。',
        'parameters': {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1}},
                       'required': ['text'], 'additionalProperties': False}}}
    try:
        await validate_native_origin(runtime, mailbox, event.scene_id)
        await runtime.event_store.save_trace(kind='live_announcement', scene_id=event.scene_id,
            ref_id=event.id, payload=audit)
        plugin = runtime.plugin_host.get_plugin('bilibili_live_sensor')
        config = plugin.config
        binding = runtime.provider_registry.resolve('conversation')
        facts = {key: event.payload[key] for key in ('member', 'bilibili_uid', 'room_id', 'title', 'url', 'started_at', 'sampled_at')}
        messages = [{'role': 'system', 'content':
            f'你是{runtime.config.identity_name}。{runtime.config.identity_persona}\n{runtime.config.identity_core}\n'
            '当前是已订阅开播公告，只根据所给真实场次写一段邀请，正确指认主播。'
            '调用 finish_announcement 提交；不要决定目标群，不写全体提及，不读取群史或创建其他工作。\n'
            + config.announcement_instructions},
            {'role': 'user', 'content': json.dumps(facts, ensure_ascii=False)}]

        async def prepare_request(trajectory, definitions):
            await validate_native_origin(runtime, mailbox, event.scene_id)
            if estimate_request(trajectory, definitions)['input_tokens'] > config.announcement_context_tokens - config.announcement_output_tokens:
                raise ValueError('公告输入超过本次配置容量')
            return trajectory

        async def finish(arguments):
            if set(arguments) != {'text'} or not isinstance(arguments['text'], str) or not arguments['text'].strip():
                raise ValueError('公告需要一段非空正文')
            decision = await _commit_expression(runtime, event, mailbox,
                [MessageSegment(type='text', text=arguments['text'])])
            audit.update(state='committed', action_ids=decision.action_ids)
            return decision

        async def execute(name, arguments):
            raise ValueError(f'公告未开放资料工具：{name}')

        async with runtime._cognition_semaphore:
            await AgentLoop(ModelGateway(binding, max_output_tokens=config.announcement_output_tokens,
                call_store=runtime.event_store, scene_id=event.scene_id, episode_id=mailbox.episode_id,
                purpose='announcement')).run(messages=messages, tool_definitions=lambda: [],
                    execute_tool=execute, terminal=terminal, finish=finish,
                    max_steps=config.announcement_max_steps, max_tool_calls=config.announcement_max_tool_calls,
                    prepare_request=prepare_request, trace=audit)
    except asyncio.CancelledError:
        audit.update(state='interrupted')
        raise
    except Exception as error:
        audit.update(state='failed', error=str(error), error_type=type(error).__name__)
    finally:
        await runtime.event_store.set_model_call_disposition(mailbox.episode_id,
            'expression' if audit['state'] == 'committed' else 'rejected')
        await runtime.event_store.save_trace(kind='live_announcement', scene_id=event.scene_id,
            ref_id=event.id, payload=audit)
