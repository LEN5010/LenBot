"""Model discovery and explicit paid probes shared by both configuration pages."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..configuration.models import Binding, Provider
from ..configuration.types import STRICT
from ..models.client import ChatModel, ModelSettings
from ..models.providers import ProviderAPI


class ProviderDraft(BaseModel):
    model_config = STRICT
    api: ProviderAPI
    base_url: str
    api_key: str | None = Field(default=None, repr=False)
    proxy: str | None = None

    def resolve(self, saved: Provider | None = None) -> Provider:
        key = self.api_key
        if key is None:
            if saved is None:
                raise ValueError('新服务商需要填写 API Key')
            if (self.api, self.base_url.rstrip('/'), self.proxy) != (saved.api, saved.base_url, saved.proxy):
                raise ValueError('服务商协议、地址或代理改变后需要重新填写 API Key')
            key = saved.api_key
        return Provider(api=self.api, base_url=self.base_url, api_key=key, proxy=self.proxy)


class ProviderCandidate(BaseModel):
    model_config = STRICT
    alias: str | None = None
    provider: ProviderDraft


class ModelCandidate(ProviderCandidate):
    binding: Binding
    kind: Literal['text', 'tools'] = 'text'


async def probe_model(provider: Provider, binding: Binding, kind: str = 'text') -> dict:
    settings = ModelSettings.from_binding(provider, binding)
    messages = [{'role': 'user', 'content': '请回复连接成功。' if kind == 'text' else '请调用 connection_check 工具完成连接测试。不要自行假设工具结果。'}]
    tools = [] if kind == 'text' else [{'type': 'function', 'function': {
        'name': 'connection_check', 'description': '返回本次连接测试的固定结果，不执行任何外部操作。',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
    replies = []
    async with ChatModel(settings) as model:
        reply = await model.complete(messages, tools)
        replies.append(reply)
        if kind == 'tools':
            if len(reply.tool_calls) != 1 or reply.tool_calls[0].name != 'connection_check' or reply.tool_calls[0].arguments != {}:
                raise ValueError('模型未按测试请求调用 connection_check；普通文本成功不能证明工具可用')
            messages.extend([reply.message, {'role': 'tool', 'tool_call_id': reply.tool_calls[0].id,
                                            'content': '连接成功，工具结果已收到。'}])
            reply = await model.complete(messages, tools)
            replies.append(reply)
        if reply.tool_calls or not reply.text.strip():
            raise ValueError('模型未返回完整文本；不能把工具请求或空响应当成连接成功')
    return {'model': binding.model, 'text': reply.text, 'kind': kind, 'calls': len(replies),
            'usage': [item.usage for item in replies],
            'scope': '验证一次文本调用' if kind == 'text' else '验证工具调用及结果续接；未验证图片或长上下文'}
