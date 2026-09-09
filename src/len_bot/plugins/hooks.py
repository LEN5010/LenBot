"""Typed plugin views at the six existing execution boundaries."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from len_bot.media.models import MessageSegment
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolResult

HookPhase = Literal['before_model', 'after_model', 'before_tool', 'after_tool', 'before_commit', 'after_delivery']
HookScope = Literal['own', 'conversation', 'work', 'scene']


class HookView(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    stop_reason: str | None = Field(default=None, min_length=1)


class BeforeModel(HookView):
    instructions: list[str] = Field(default_factory=list)
    materials: list[ToolResult] = Field(default_factory=list)
    tool_names: list[str]


class ToolCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str
    name: str
    arguments: dict[str, Any]


class AfterModel(HookView):
    candidates: list[ToolCandidate]


class BeforeTool(HookView):
    name: str
    arguments: dict[str, Any]


class AfterTool(HookView):
    name: str
    # The original displayed receipt is read-only. A separate plugin view can
    # explain or reorganize it without falsifying evidence coverage or status.
    original: dict[str, Any]
    notes: list[str] = Field(default_factory=list)
    view: dict[str, Any] | None = None


class BeforeCommit(HookView):
    messages: list[list[MessageSegment]]


class AfterDelivery(HookView):
    receipt: dict[str, Any]


HOOK_VIEWS = {'before_model': BeforeModel, 'after_model': AfterModel,
    'before_tool': BeforeTool, 'after_tool': AfterTool,
    'before_commit': BeforeCommit, 'after_delivery': AfterDelivery}


@dataclass(frozen=True)
class PluginHookDefinition:
    plugin_id: str
    id: str
    phase: HookPhase
    handler: Callable[[HookView, PluginCallContext], Awaitable[HookView | None]]
    scope: HookScope
    priority: int
    order: int

    def record(self):
        return {'id': self.id, 'phase': self.phase, 'scope': self.scope, 'priority': self.priority}


class PluginHookStopped(RuntimeError):
    pass


class PluginRunHooks:
    def __init__(self, host, call: Callable[[], PluginCallContext], audit: dict, *, only_plugin=None):
        self.host, self.call, self.audit, self.only_plugin = host, call, audit, only_plugin

    async def apply(self, phase: HookPhase, view: HookView, *, call_override=None):
        from len_bot.cognition.agent_loop import _trace_value
        call = call_override or self.call()
        for hook in self.host.applicable_hooks(phase, call):
            if self.only_plugin is not None and hook.plugin_id != self.only_plugin:
                continue
            before = view.model_dump(mode='json')
            record = {**hook.record(), 'plugin_id': hook.plugin_id, 'state': 'started'}
            self.audit.setdefault('hooks', []).append(record)
            try:
                context = replace(call, plugin=self.host.context_for(hook.plugin_id))
                result = await hook.handler(view.model_copy(deep=True), context)
                if result is not None:
                    if not isinstance(result, HOOK_VIEWS[phase]):
                        raise TypeError(f'Hook {hook.id} must return {HOOK_VIEWS[phase].__name__} or None')
                    view = HOOK_VIEWS[phase].model_validate(result.model_dump(), strict=True)
                after = view.model_dump(mode='json')
                record.update(state='changed' if before != after else 'observed')
                if before != after:
                    record.update(before=_trace_value(before), after=_trace_value(after))
                if view.stop_reason:
                    record['state'] = 'stopped'
                    raise PluginHookStopped(f'{hook.plugin_id}/{hook.id}: {view.stop_reason}')
            except Exception as error:
                record.update(error=str(error))
                if record['state'] != 'stopped':
                    record['state'] = 'failed'
                raise
        return view

    async def before_model(self, messages, definitions, terminal_name):
        original = [item['function']['name'] for item in definitions]
        view = await self.apply('before_model', BeforeModel(tool_names=original))
        if (len(view.tool_names) != len(set(view.tool_names)) or set(view.tool_names) - set(original)
                or terminal_name not in view.tool_names):
            raise ValueError('before_model may only select currently allowed tools, retaining the terminal')
        messages = [copy.deepcopy(message) for message in messages
            if message.get('_context_section') not in {'plugin_instructions', 'plugin_material'}]
        for instruction in view.instructions:
            messages.append({'role': 'developer', 'content': instruction, '_context_section': 'plugin_instructions'})
        for material in view.materials:
            messages.append({'role': 'user', 'content': json.dumps({'kind': 'plugin_material',
                'observation': material.model_dump(mode='json', exclude_none=True)}, ensure_ascii=False),
                '_context_section': 'plugin_material'})
        return messages, [item for item in definitions if item['function']['name'] in view.tool_names]

    async def after_model(self, entries):
        view = await self.apply('after_model', AfterModel(candidates=[ToolCandidate(id=call.id,
            name=call.name, arguments=arguments) for call, arguments, _ in entries]))
        if [(item.id, item.name) for item in view.candidates] != [(call.id, call.name) for call, _, _ in entries]:
            raise ValueError('after_model must preserve actual call identities, names and order')
        return [(call, json.loads(json.dumps(candidate.arguments, allow_nan=False)), record)
                for (call, _, record), candidate in zip(entries, view.candidates)]

    async def before_tool(self, name, arguments, tool_call_id):
        view = await self.apply('before_tool', BeforeTool(name=name, arguments=arguments),
            call_override=replace(self.call(), tool_call_id=tool_call_id))
        if view.name != name:
            raise ValueError('before_tool cannot substitute another tool')
        return json.loads(json.dumps(view.arguments, allow_nan=False))

    async def after_tool(self, name, content, tool_call_id):
        original = json.loads(content)
        if not isinstance(original, dict):
            raise TypeError('The displayed tool receipt must be an object')
        view = await self.apply('after_tool', AfterTool(name=name, original=copy.deepcopy(original)),
            call_override=replace(self.call(), tool_call_id=tool_call_id))
        if view.name != name or view.original != original:
            raise ValueError('after_tool cannot rewrite the original observation or operation receipt')
        if view.notes or view.view is not None:
            original['plugin_view'] = {'notes': view.notes, 'data': view.view,
                                       'evidence_kind': 'model_view', 'original_observation_retained': True}
        return json.dumps(original, ensure_ascii=False)

    async def before_commit(self, outcome):
        view = await self.apply('before_commit', BeforeCommit(messages=[message.segments for message in outcome.message_proposals]))
        if len(view.messages) != len(outcome.message_proposals):
            raise ValueError('before_commit may change segments, but cannot add or remove message ownership')
        data = outcome.model_dump()
        for message, segments in zip(data['message_proposals'], view.messages):
            message['segments'] = [segment.model_dump() for segment in segments]
        return type(outcome).model_validate(data)

    async def after_delivery(self, event):
        original = event.model_dump(mode='json')
        view = await self.apply('after_delivery', AfterDelivery(receipt=copy.deepcopy(original)))
        if view.receipt != original:
            raise ValueError('after_delivery cannot rewrite the saved receipt')
