"""Credential-bearing requests stay in this fixed-endpoint connector."""
from __future__ import annotations

import json
from enum import StrEnum
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from len_bot.runtime.capabilities import Capability, CapabilitySubject
from len_bot.tools.results import ObservationProvenance, ToolNextCall, ToolResult, ToolSource


class Endpoint(StrEnum):
    NAV = '/x/web-interface/nav'
    DYNAMIC = '/x/polymer/web-dynamic/v1/feed/space'
    LIKE_STATE = '/x/web-interface/archive/has/like'
    FOLDER_STATE = '/x/v3/fav/folder/created/list-all'
    LIKE = '/x/web-interface/archive/like'
    FAVORITE = '/x/v3/fav/resource/deal'


class AccountResponse(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    code: int
    data: dict | list | int | None = None


class LoginIdentity(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    isLogin: bool
    mid: int = Field(gt=0)


class DynamicText(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    text: str


class DynamicAuthor(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    mid: int
    name: str
    pub_ts: int | None = None


class DynamicBody(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    desc: DynamicText | None = None


class DynamicModules(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    module_author: DynamicAuthor
    module_dynamic: DynamicBody


class DynamicItem(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    id_str: str
    type: str
    modules: DynamicModules


class DynamicPage(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    items: list[DynamicItem]
    offset: str
    has_more: bool


class AccountReadError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


class AccountConnector:
    def __init__(self, context, config):
        self.context, self.config = context, config
        self.runtime, self.store = context._runtime, context.event_store
        # Never shared with public reads, browser, ffmpeg or free code workers.
        self.client = httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=False,
            follow_redirects=False, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})

    async def close(self):
        await self.client.aclose()

    def available(self, call, capability=Capability.BILIBILI_AUTHENTICATED_READ):
        current = self.runtime.config_store.current.plugins.get('bilibili_content')
        if (current is None or not current.enabled or current.parsed_config != self.config
                or not self.config.account_uid or not self.config.sessdata
                or call.role != 'work' or not call.job_id or not call.requester_qq_uid or call.public_research):
            return False
        if capability == Capability.BILIBILI_AUTHENTICATED_READ and not self.config.authenticated_read_enabled:
            return False
        return self.store.capability_authority.check(capability,
            CapabilitySubject('human', call.requester_qq_uid, call.scene_id, None), now=self.store.clock()).allowed

    async def job(self, call, capability=Capability.BILIBILI_AUTHENTICATED_READ):
        await self.context._host.validate_call(call)
        if not self.available(call, capability):
            raise AccountReadError('capability_denied', '登录态接口缺少当前工作、账号配置或独立能力授权')
        if self.runtime.config_store.path.stat().st_mode & 0o077:
            raise AccountReadError('credential_file_permissions', '根配置含账号凭据，须由运营停机后设置为 0600')
        if any(char in self.config.sessdata for char in '\r\n;'):
            raise AccountReadError('credentials_invalid', 'SESSDATA 格式无效')
        job = await self.store.get_job(call.job_id, call.scene_id)
        if (job is None or job['status'] != 'processing' or job['revision'] != call.job_revision
                or job['requester_qq_uid'] != call.requester_qq_uid or not call.tool_call_id):
            raise AccountReadError('work_changed', '登录态动作已不属于当前运行工作修订')
        deadline = (job.get('budget') or {}).get('deadline_at')
        if deadline is not None and deadline <= self.store.clock():
            raise AccountReadError('work_expired', '原工作期限已结束')
        return job

    async def request(self, endpoint: Endpoint, params=None, *, form=None, envelope=False):
        if not isinstance(endpoint, Endpoint):
            raise ValueError('账号接口需要固定类型端点')
        writes = {Endpoint.LIKE, Endpoint.FAVORITE}
        if (endpoint in writes) != (form is not None) or endpoint in writes and params is not None:
            raise ValueError('账号端点与请求方法不匹配')
        cookies = {'SESSDATA': self.config.sessdata}
        if form is not None:
            cookies['bili_jct'] = self.config.bili_jct
        if self.config.buvid3:
            cookies['buvid3'] = self.config.buvid3
        if any(any(char in value for char in '\r\n;') for value in cookies.values()):
            raise AccountReadError('credentials_invalid', 'Cookie 格式无效')
        async with self.client.stream('POST' if form is not None else 'GET',
                'https://api.bilibili.com' + endpoint.value, params=params, data=form,
                headers={'Cookie': '; '.join(key + '=' + value for key, value in cookies.items()),
                         'Accept-Encoding': 'identity'}) as response:
            if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                raise AccountReadError('account_http_error', f'账号接口 HTTP {response.status_code} 或内容编码不受支持')
            buffer = bytearray()
            async for chunk in response.aiter_raw():
                buffer.extend(chunk)
                if len(buffer) > 2_000_000:
                    raise AccountReadError('resource_limit', '账号响应超过 2MB')
        body = AccountResponse.model_validate_json(bytes(buffer))
        if envelope:
            return body
        if body.code != 0:
            raise AccountReadError('bilibili_' + str(body.code), 'B 站账号接口返回错误码 ' + str(body.code))
        return body.data

    async def verify_identity(self):
        identity = LoginIdentity.model_validate(await self.request(Endpoint.NAV))
        if not identity.isLogin or identity.mid != self.config.account_uid:
            raise AccountReadError('account_mismatch', '登录身份与运营配置的账号不一致，停止动作')

    async def dynamic(self, args, call):
        action_id = None
        try:
            job = await self.job(call)
            review = await self.runtime.action_reviewer.request(job=job, native_call_id=call.tool_call_id,
                action_type='authenticated_read', target=f'bilibili:{self.config.account_uid}:space:{args.mid}',
                parameters={'account_uid': self.config.account_uid, 'host_mid': args.mid, 'offset': args.offset})
            action_id = review.action_id
            await self.runtime.action_reviewer.approve(review)
            await self.job(call)
            await self.verify_identity()
            await self.job(call)
            params = {'host_mid': args.mid, 'offset': args.offset}
            page = DynamicPage.model_validate(await self.request(Endpoint.DYNAMIC, params))
            await self.job(call)
            records = [{'id': item.id_str, 'type': item.type, 'author': item.modules.module_author.model_dump(),
                        'text': item.modules.module_dynamic.desc.text if item.modules.module_dynamic.desc else None,
                        'url': 'https://t.bilibili.com/' + item.id_str} for item in page.items]
            result = ToolResult(status='ok' if records else 'no_results', evidence_kind='external',
                content=json.dumps({'account_uid': self.config.account_uid, 'items': records,
                    'coverage': '仅此页动态正文；转发原文、文章和媒体附件尚未读取', 'has_more': page.has_more}, ensure_ascii=False),
                sources=[ToolSource(url='https://api.bilibili.com' + Endpoint.DYNAMIC.value + '?' + urlencode(params))],
                coverage='account_dynamic_page', provenance=ObservationProvenance(access='account'),
                source_next_call=ToolNextCall(name='get_dynamic_feed', arguments={'mid': args.mid, 'offset': page.offset})
                    if page.has_more and page.offset else None)
        except (ValueError, PermissionError, OSError, httpx.HTTPError) as error:
            # Do not serialize raw SDK/HTTP exceptions, bodies or credentials.
            code = error.code if isinstance(error, AccountReadError) else type(error).__name__
            result = ToolResult.failure(str(error) if isinstance(error, AccountReadError)
                else '登录态读取失败，未返回账号资料', code)
            result.provenance = ObservationProvenance(access='account')
        await self.store.save_trace(kind='account_read', scene_id=call.scene_id, ref_id=action_id or call.tool_call_id,
            payload={'action_id': action_id, 'account_uid': self.config.account_uid, 'job_id': call.job_id,
                     'job_revision': call.job_revision, 'target_mid': args.mid, 'status': result.status,
                     'error_code': result.error_code})
        return result
