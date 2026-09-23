"""Per-call locations and explicitly declared static components."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class _PromptComponent:
    component_id: str
    revision: int
    start: int
    text: str


class _RecordedToolDefinition(dict):
    """Keep a code-owned definition beside its ordinary wire dictionary.

    Only fixed core declarations use this type. Attributes survive copying
    but do not participate in JSON serialization or token estimation.
    """

    def __init__(self, definition: dict, *, component_id: str, revision: int):
        super().__init__(definition)
        self.component_id = component_id
        self.revision = revision
        self.definition_json = json.dumps(definition, ensure_ascii=False, allow_nan=False)


class _LocatedPluginToolDefinition(dict):
    """Carry host-owned provenance, never persist a plugin's dynamic schema."""

    def __init__(self, definition: dict, *, plugin_id: str, plugin_version: str, api_version: int):
        super().__init__(definition)
        self.plugin_id = plugin_id
        self.plugin_version = plugin_version
        self.api_version = api_version
        self.declared_json = json.dumps(definition, ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class _RequestLocation:
    event_id: str | None = None
    ref: str | None = None
    text_range: dict[str, int] | None = None
    original_ranges: list[dict[str, str | int]] = field(default_factory=list)
    omitted: bool | None = None
    omission_reason: str | None = None
    image_assets: dict[int, str] = field(default_factory=dict)
    result_locator_status: str | None = None
    result_locators: list[dict[str, Any]] | None = None
    summary_ref_status: str | None = None
    summary_refs: list[dict[str, Any]] | None = None
    prompt_components: tuple[_PromptComponent, ...] = ()
    tool_presentations: list[dict[str, str | int]] | None = None


def _prompt_record(content, components):
    records = []
    for component in components:
        if not isinstance(component, _PromptComponent):
            raise TypeError('Invalid internal prompt component')
        end = component.start + len(component.text)
        matches = (isinstance(content, str) and 0 <= component.start <= end <= len(content)
                   and content[component.start:end] == component.text)
        records.append({'component_id': component.component_id, 'revision': component.revision,
            'status': 'retained' if matches else 'changed_after_declaration',
            'text_range': {'start': component.start, 'end': end} if matches else None,
            'snapshot_text': component.text if matches else None})
    return records


def _tool_record(index, tool):
    record = {'index': index, 'type': tool.get('type'), 'name': (tool.get('function') or {}).get('name'),
              'definition': {'status': 'not_recorded'}}
    if isinstance(tool, _RecordedToolDefinition):
        matches = json.loads(tool.definition_json) == tool
        record['definition'] = {'component_id': tool.component_id, 'revision': tool.revision,
            'status': 'retained' if matches else 'changed_after_declaration',
            'snapshot_json': tool.definition_json if matches else None}
    elif isinstance(tool, _LocatedPluginToolDefinition):
        matches = json.loads(tool.declared_json) == tool
        record['definition'] = {
            'status': 'origin_recorded' if matches else 'changed_after_declaration',
            'plugin': {'id': tool.plugin_id, 'version': tool.plugin_version,
                       'api_version': tool.api_version},
            'snapshot_json': None,
        }
    return record


def prepare_request_record(request: dict[str, Any]) -> dict[str, Any]:
    """Remove the private sidecar from a copied request before estimation/send.

    Locations are attached after final context fitting. Other callers have no
    sidecar: their final roles and media positions are still observable, while
    source references stay absent. No body is parsed to guess a source.
    """
    messages = []
    for index, message in enumerate(request['messages']):
        location = message.pop('_request_location', None)
        if location is not None and not isinstance(location, _RequestLocation):
            raise TypeError('Invalid internal request location')
        content = message.get('content')
        prompt_components = _prompt_record(content, location.prompt_components if location else ())
        images = []
        if isinstance(content, list):
            for part_index, part in enumerate(content):
                if isinstance(part, dict) and part.get('type') in {'image_url', 'input_image'}:
                    images.append({'part_index': part_index, 'type': part['type'],
                        'asset_id': location.image_assets.get(part_index) if location else None})
        entry = {
            'index': index, 'role': message['role'], 'section': message.get('_context_section'),
            'event_id': location.event_id if location else None,
            'ref': location.ref if location else None,
            'text_range': location.text_range if location else None,
            'original_ranges': location.original_ranges if location else None,
            'omitted': location.omitted if location else None,
            'omission_reason': location.omission_reason if location else None,
            'tool_call_id': message.get('tool_call_id'),
            'assistant_tool_calls': [{'id': call.get('id'), 'name': (call.get('function') or {}).get('name')}
                for call in message.get('tool_calls') or []],
            'text_chars': len(content) if isinstance(content, str) else None,
            'part_types': [part.get('type') if isinstance(part, dict) else None for part in content]
                if isinstance(content, list) else None,
            'images': images,
            'prompt_components': prompt_components,
            'tool_presentations': location.tool_presentations if location else None,
        }
        if location and location.result_locator_status is not None:
            entry['result_locator_status'] = location.result_locator_status
            entry['result_locators'] = location.result_locators
        if location and location.summary_ref_status is not None:
            entry['summary_ref_status'] = location.summary_ref_status
            entry['summary_refs'] = location.summary_refs
        messages.append(entry)
    choice = request['tool_choice']
    tools = [_tool_record(index, tool) for index, tool in enumerate(request['tools'])]
    # The client receives plain dictionaries without private snapshot attributes.
    request['tools'] = [dict(tool) for tool in request['tools']]
    return {
        'format_version': 5,
        'boundary': 'before_client_send',
        'settings': {key: request.get(key) for key in ('model', 'reasoning_effort', 'max_completion_tokens', 'stream')},
        'tool_choice': ({'type': choice.get('type'), 'name': (choice.get('function') or {}).get('name')}
            if isinstance(choice, dict) else choice),
        'messages': messages,
        'tools': tools,
        'not_retained': ['dynamic_message_bodies', 'undeclared_prompt_components', 'undeclared_tool_definitions',
                         'dynamic_plugin_tool_definitions',
                         'tool_arguments', 'media_bodies', 'provider_wire_body'],
    }
