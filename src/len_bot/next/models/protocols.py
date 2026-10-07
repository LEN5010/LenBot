"""Native wire formats; the scene stores readable messages plus opaque continuation."""
from __future__ import annotations

import copy
import json
import uuid
from urllib.parse import quote

from .client import ModelSettings, ModelReply, ModelProtocolError, parse_chat_completion, parse_token_usage


def _identity(settings: ModelSettings) -> dict:
    return {'api': settings.api, 'model': settings.model, 'base_url': settings.base_url}


def _native(message: dict, settings: ModelSettings):
    if settings.api == 'openai-chat' and message.get('_binding') == _identity(settings):
        return {k: v for k, v in message.items() if k != '_binding'}
    continuation = message.get('_continuation')
    if continuation is not None and continuation['binding'] == _identity(settings):
        return copy.deepcopy(continuation['data'])
    return None


def _history(settings: ModelSettings, messages: list[dict]) -> list[dict]:
    """Completed tool groups from another binding become readable historical facts."""
    result = []
    pending = {}
    transcript = []
    for message in messages:
        calls = message.get('tool_calls') or []
        foreign = (calls and _native(message, settings) is None
                   and (settings.api != 'openai-chat' or '_continuation' in message or '_binding' in message))
        if foreign:
            pending.update({call['id']: call for call in calls})
            transcript.append({key: value for key, value in message.items() if key in {'role', 'content', 'tool_calls'}})
        elif message['role'] == 'tool' and message['tool_call_id'] in pending:
            pending.pop(message['tool_call_id'])
            transcript.append(message)
            if not pending:
                result.append({'role': 'user', 'content': '此前模型已经执行的工具及实际结果（历史资料）：\n' + json.dumps(transcript, ensure_ascii=False)})
                transcript = []
        else:
            result.append(message)
    if pending:
        raise ValueError('存在尚未完成的工具组，不能跨模型绑定续接')
    return result


def _parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    return [] if content is None else content


def _image(url: str, api: str) -> dict:
    if url.startswith('data:'):
        meta, data = url.split(',', 1)
        mime = meta[5:].removesuffix(';base64')
        if not meta.endswith(';base64'):
            raise ValueError('图片 data URL 必须使用 base64')
        if api == 'anthropic':
            return {'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}}
        return {'inlineData': {'mimeType': mime, 'data': data}}
    if api == 'anthropic':
        return {'type': 'image', 'source': {'type': 'url', 'url': url}}
    raise ValueError('Gemini 图片需要内联 data URL；不能把普通网页图片地址当成 Files API 地址')


def build_request(settings: ModelSettings, messages: list[dict], tools: list[dict], *,
                  max_output_tokens: int | None = None) -> tuple[str, dict]:
    messages = _history(settings, messages)
    output = settings.max_output_tokens if max_output_tokens is None else max_output_tokens
    api = settings.api
    payload = {'model': settings.model}
    if api == 'openai-chat':
        payload.update(messages=[(
            {k: v for k, v in m.items() if k not in {'_continuation', '_binding'}}
            if not ({'_continuation', '_binding'} & m.keys()) or _native(m, settings) is not None
            else {k: v for k, v in m.items() if k in {'role', 'content', 'tool_calls', 'tool_call_id'}}
        ) for m in messages],
                       stream=False, **{settings.output_token_field: output})
        if tools:
            payload['tools'] = tools
        if settings.reasoning_effort is not None:
            payload['reasoning_effort'] = settings.reasoning_effort
        path = 'chat/completions'
    elif api == 'openai-responses':
        items = []
        for message in messages:
            native = _native(message, settings)
            if native is not None:
                items.extend(native)
            elif message['role'] == 'tool':
                items.append({'type': 'function_call_output', 'call_id': message['tool_call_id'], 'output': message['content']})
            else:
                content = []
                for part in _parts(message['content']):
                    if part['type'] == 'text':
                        content.append({'type': 'output_text' if message['role'] == 'assistant' else 'input_text', 'text': part['text']})
                    elif part['type'] == 'image_url':
                        content.append({'type': 'input_image', 'image_url': part['image_url']['url'], 'detail': part['image_url'].get('detail', 'auto')})
                    else:
                        raise ValueError(f'不支持的内容块：{part["type"]}')
                if content:
                    items.append({'role': message['role'], 'content': content})
        payload.update(input=items, store=False, include=['reasoning.encrypted_content'], max_output_tokens=output, stream=False)
        if tools:
            payload['tools'] = [{'type': 'function', **tool['function']} for tool in tools]
        if settings.reasoning_effort is not None:
            payload['reasoning'] = {'effort': settings.reasoning_effort}
        path = 'responses'
    else:
        system, contents, names = [], [], {}
        for message in messages:
            role = message['role']
            if role in {'system', 'developer'}:
                system.extend(_parts(message['content']))
                continue
            native = _native(message, settings)
            if native is not None:
                content = native if api == 'anthropic' else native['parts']
                for call in message.get('tool_calls') or []:
                    names[call['id']] = call['function']['name']
            elif role == 'tool':
                if api == 'anthropic':
                    content = [{'type': 'tool_result', 'tool_use_id': message['tool_call_id'], 'content': message['content']}]
                else:
                    call_id = message['tool_call_id']
                    response = {'name': names[call_id], 'response': {'result': message['content']}}
                    if not call_id.startswith('lenbot_gemini_'):
                        response['id'] = call_id
                    content = [{'functionResponse': response}]
            else:
                content = []
                for part in _parts(message['content']):
                    if part['type'] == 'text':
                        content.append({'type': 'text', 'text': part['text']} if api == 'anthropic' else {'text': part['text']})
                    elif part['type'] == 'image_url':
                        content.append(_image(part['image_url']['url'], api))
                    else:
                        raise ValueError(f'不支持的内容块：{part["type"]}')
            native_role = ('assistant' if role == 'assistant' else 'user') if api == 'anthropic' else ('model' if role == 'assistant' else 'user')
            key = 'content' if api == 'anthropic' else 'parts'
            if content:
                if contents and contents[-1]['role'] == native_role:
                    contents[-1][key].extend(content)
                else:
                    contents.append({'role': native_role, key: content})
        if api == 'anthropic':
            payload.update(messages=contents, max_tokens=output, stream=False)
            if system:
                payload['system'] = system
            if tools:
                payload['tools'] = [{'name': t['function']['name'], 'description': t['function'].get('description', ''), 'input_schema': t['function']['parameters']} for t in tools]
            if settings.thinking_budget_tokens is not None:
                payload['thinking'] = {'type': 'enabled', 'budget_tokens': settings.thinking_budget_tokens}
            elif settings.reasoning_effort is not None:
                payload.update(thinking={'type': 'adaptive'}, output_config={'effort': settings.reasoning_effort})
            path = 'messages'
        else:
            payload = {'contents': contents, 'generationConfig': {'maxOutputTokens': output}}
            if system:
                payload['systemInstruction'] = {'parts': [{'text': p['text']} for p in system]}
            if tools:
                payload['tools'] = [{'functionDeclarations': [{'name': t['function']['name'], 'description': t['function'].get('description', ''), 'parametersJsonSchema': t['function']['parameters']} for t in tools]}]
            if settings.thinking_budget_tokens is not None:
                payload['generationConfig']['thinkingConfig'] = {'thinkingBudget': settings.thinking_budget_tokens}
            elif settings.reasoning_effort is not None:
                payload['generationConfig']['thinkingConfig'] = {'thinkingLevel': settings.reasoning_effort}
            path = f'models/{quote(settings.model.removeprefix("models/"), safe="")}:generateContent'
    if settings.temperature is not None:
        (payload['generationConfig'] if api == 'gemini' else payload)['temperature'] = settings.temperature
    return path, payload


def normalized_usage(api: str, usage: dict | None) -> dict | None:
    if usage is None or api == 'openai-chat':
        return usage
    if not isinstance(usage, dict):
        raise ValueError('usage must be an object or null')
    def count(key):
        value = usage.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'usage.{key} must be a nonnegative integer or null')
        return value
    if api == 'openai-responses':
        prompt, output = count('input_tokens'), count('output_tokens')
        details = usage.get('input_tokens_details')
        if details is not None and not isinstance(details, dict):
            raise ValueError('usage.input_tokens_details must be an object or null')
        cached = None if details is None else details.get('cached_tokens')
    elif api == 'anthropic':
        prompt, output = count('input_tokens'), count('output_tokens')
        cached, created = count('cache_read_input_tokens'), count('cache_creation_input_tokens')
        if prompt is not None:
            prompt += (cached or 0) + (created or 0)
    else:
        prompt, output = count('promptTokenCount'), count('candidatesTokenCount')
        thoughts, cached = count('thoughtsTokenCount'), count('cachedContentTokenCount')
        if output is not None:
            output += thoughts or 0
    return {**usage, 'prompt_tokens': prompt, 'completion_tokens': output, 'prompt_tokens_details': {'cached_tokens': cached}}


def parse_reply(settings: ModelSettings, body: object) -> ModelReply:
    if settings.api == 'openai-chat':
        reply = parse_chat_completion(body)
        return ModelReply({**reply.message, '_binding': _identity(settings)}, reply.text, reply.tool_calls,
                          reply.finish_reason, reply.usage, reply.token_usage, reply.response)
    usage = tokens = None
    try:
        api = settings.api
        if not isinstance(body, dict):
            raise ValueError('expected a response object')
        usage = normalized_usage(api, body.get('usageMetadata' if api == 'gemini' else 'usage'))
        tokens = parse_token_usage(usage)
        texts, calls = [], []
        if api == 'openai-responses':
            if body['status'] != 'completed':
                raise ValueError(f'incomplete response: {body["status"]}; {body.get("incomplete_details")}')
            native = body['output']
            for item in native:
                if item['type'] == 'message':
                    for block in item['content']:
                        if block['type'] == 'output_text':
                            texts.append(block['text'])
                        elif block['type'] == 'refusal':
                            raise ValueError(f'model refusal: {block["refusal"]}')
                elif item['type'] == 'function_call':
                    calls.append({'id': item['call_id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}})
                elif item['type'] != 'reasoning':
                    raise ValueError(f'unsupported response item: {item["type"]}')
        elif api == 'anthropic':
            if body['stop_reason'] not in {'end_turn', 'tool_use'}:
                raise ValueError(f'incomplete or unsupported stop_reason: {body["stop_reason"]}')
            if body['role'] != 'assistant':
                raise ValueError('expected an assistant message')
            native = body['content']
            for item in native:
                if item['type'] == 'text':
                    texts.append(item['text'])
                elif item['type'] == 'tool_use':
                    calls.append({'id': item['id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': json.dumps(item['input'], allow_nan=False)}})
                elif item['type'] not in {'thinking', 'redacted_thinking'}:
                    raise ValueError(f'unsupported content block: {item["type"]}')
            if (body['stop_reason'] == 'tool_use') != bool(calls):
                raise ValueError('stop_reason and tool calls disagree')
        else:
            candidate = body['candidates'][0]
            if candidate['finishReason'] != 'STOP':
                raise ValueError(f'incomplete or unsupported finishReason: {candidate["finishReason"]}')
            native = candidate['content']
            if native['role'] != 'model':
                raise ValueError('expected model content')
            for part in native['parts']:
                if 'text' in part and not part.get('thought'):
                    texts.append(part['text'])
                elif 'functionCall' in part:
                    call = part['functionCall']
                    calls.append({'id': call.get('id') or 'lenbot_gemini_' + uuid.uuid4().hex,
                                  'type': 'function', 'function': {'name': call['name'], 'arguments': json.dumps(call.get('args', {}), allow_nan=False)}})
        message = {'role': 'assistant', 'content': ''.join(texts), 'tool_calls': calls or None}
        reason = 'tool_calls' if calls else 'stop'
        reply = parse_chat_completion({'choices': [{'message': message, 'finish_reason': reason}], 'usage': usage})
        message['_continuation'] = {'binding': _identity(settings), 'data': copy.deepcopy(native)}
        return ModelReply(message, reply.text, reply.tool_calls, reason, usage, tokens, copy.deepcopy(body))
    except (KeyError, IndexError, TypeError, ValueError, ModelProtocolError) as error:
        raise ModelProtocolError(f'Invalid {settings.api} reply: {error}; response fragment: {json.dumps(body, ensure_ascii=False, default=repr)[:500]}', response=body, usage=usage, token_usage=tokens) from error
