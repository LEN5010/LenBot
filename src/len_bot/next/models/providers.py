"""Protocol metadata and transport shared by setup, runtime and worker calls."""
from __future__ import annotations

import asyncio
from typing import Literal
from urllib.parse import urlsplit

import httpx

ChatAPI = Literal['openai-chat', 'openai-responses', 'anthropic', 'gemini']
ProviderAPI = Literal['openai-chat', 'openai-responses', 'anthropic', 'gemini', 'openai-audio', 'openai-embeddings']
CHAT_APIS = {'openai-chat', 'openai-responses', 'anthropic', 'gemini'}
PROTOCOLS = [
    {'api': 'openai-chat', 'title': 'OpenAI 兼容聊天', 'base_url': '',
     'roles': ['mind', 'vision', 'memory', 'learner', 'worker', 'asr', 'embedding'],
     'description': 'Chat Completions；兼容服务的工具、图片、语音和向量能力需以服务商说明为准。'},
    {'api': 'openai-responses', 'title': 'OpenAI Responses', 'base_url': 'https://api.openai.com/v1',
     'roles': ['mind', 'vision', 'memory', 'learner', 'worker'],
     'description': '原生 Responses；保留工具 ID 与加密推理续接，使用无服务端历史的请求。'},
    {'api': 'anthropic', 'title': 'Anthropic Messages', 'base_url': 'https://api.anthropic.com/v1',
     'roles': ['mind', 'vision', 'memory', 'learner', 'worker'],
     'description': '原生 Messages；保留思考与签名。图片支持 URL 或内联数据。'},
    {'api': 'gemini', 'title': 'Gemini GenerateContent', 'base_url': 'https://generativelanguage.googleapis.com/v1beta',
     'roles': ['mind', 'vision', 'memory', 'learner', 'worker'],
     'description': 'Gemini API（非 Vertex）；保留 thoughtSignature。图片使用内联数据。'},
    {'api': 'openai-audio', 'title': 'OpenAI 兼容语音转写', 'base_url': '',
     'roles': ['asr'], 'description': '仅 audio/transcriptions，不能用于聊天。'},
    {'api': 'openai-embeddings', 'title': 'OpenAI 兼容向量', 'base_url': '',
     'roles': ['embedding'], 'description': '仅 embeddings，不能用于聊天。'},
]


def valid_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in {'http', 'https'} or not parts.netloc
            or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
        raise ValueError('must be an HTTP(S) URL without credentials, query or fragment')
    return value.rstrip('/')


def auth_headers(api: ProviderAPI, key: str) -> dict[str, str]:
    if api == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    if api == 'gemini':
        return {'x-goog-api-key': key}
    return {'Authorization': f'Bearer {key}'}


def http_client(*, base_url: str, api_key: str, api: ProviderAPI,
                timeout_seconds: float, proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url.rstrip('/') + '/', timeout=timeout_seconds,
        trust_env=False, follow_redirects=False,
        transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False, proxy=proxy),
        headers=auth_headers(api, api_key))


async def list_models(provider, *, timeout_seconds: float = 30) -> list[dict]:
    """Read the provider's model catalog; capability limits are never guessed."""
    from .client import ModelHTTPError, ModelProtocolError
    items = []
    params = {}
    async with asyncio.timeout(timeout_seconds), http_client(base_url=provider.base_url, api_key=provider.api_key,
                           api=provider.api, timeout_seconds=timeout_seconds, proxy=provider.proxy) as client:
        while True:
            response = await client.get('models', params=params)
            if not response.is_success:
                raise ModelHTTPError(f'Model list HTTP {response.status_code}: {response.text}')
            body = response.text
            try:
                body = response.json()
                rows = body['models' if provider.api == 'gemini' else 'data']
                if not isinstance(rows, list):
                    raise ValueError('expected a model array')
                for row in rows:
                    name = row['name' if provider.api == 'gemini' else 'id']
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError('model ID must be a nonempty string')
                    if provider.api == 'gemini':
                        if 'generateContent' not in row['supportedGenerationMethods']:
                            continue
                        name = name.removeprefix('models/')
                    item = {'id': name, 'name': row.get('displayName', row.get('display_name', name))}
                    if provider.api == 'gemini':
                        item.update(context_window_tokens=row.get('inputTokenLimit'), max_output_tokens=row.get('outputTokenLimit'))
                    items.append(item)
                if provider.api == 'gemini' and body.get('nextPageToken'):
                    params = {'pageToken': body['nextPageToken']}
                elif provider.api == 'anthropic' and body.get('has_more'):
                    params = {'after_id': body['last_id']}
                else:
                    break
            except (KeyError, TypeError, ValueError) as error:
                raise ModelProtocolError(f'Invalid model list: {error}; response fragment: {response.text[:500]}', response=body) from error
    return items
