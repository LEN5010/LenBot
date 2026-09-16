"""Explicit state changes through the credential-owning, fixed-endpoint connector."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from len_bot.runtime.capabilities import Capability
from len_bot.runtime.platform_actions import actions_for, register_action, record_outcome, reserve_attempt
from len_bot.tools.results import ObservationProvenance, ToolResult, ToolSource
from .account import AccountReadError, Endpoint


class LikeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    resource_id: int = Field(gt=0, description='已确认的视频 AV 正整数 ID')
    desired_state: bool = Field(description='true 点赞，false 取消点赞；明确设置状态，不做 toggle')


class FavoriteInput(LikeInput):
    collection_id: int = Field(gt=0, description='运营允许的本账号收藏夹完整 ID')


class Folder(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    id: int
    mid: int
    fav_state: Literal[0, 1]


class FolderList(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    count: int = Field(ge=0)
    folders: list[Folder] | None = Field(alias='list')


class AccountActions:
    def __init__(self, connector):
        self.connector = connector
        self.store = connector.store
        self.locks: dict[tuple[int, int], asyncio.Lock] = {}

    def available(self, call, capability):
        config = self.connector.config
        if not self.connector.available(call, capability) or not re.fullmatch(r'[a-fA-F0-9]{32}', config.bili_jct):
            return False
        if capability == Capability.BILIBILI_LIKE:
            return config.daily_like_limit > 0 and bool(config.buvid3)
        return config.daily_favorite_limit > 0 and bool(config.allowed_collection_ids)

    async def current_state(self, resource_id, action_type, collection_id=None):
        if action_type == 'bilibili_like':
            state = await self.connector.request(Endpoint.LIKE_STATE, {'aid': resource_id})
            if type(state) is not int or state not in {0, 1}:
                raise AccountReadError('invalid_like_state', '点赞状态接口未返回 0/1')
            # Zero excludes recent likes only; never interpret it as a reliable unset.
            return True if state == 1 else None
        if collection_id not in self.connector.config.allowed_collection_ids:
            raise AccountReadError('collection_denied', '收藏夹未在运营配置的允许列表中')
        page = FolderList.model_validate(await self.connector.request(Endpoint.FOLDER_STATE,
            {'up_mid': self.connector.config.account_uid, 'type': 2, 'rid': resource_id}))
        folders = page.folders or []
        if page.count != len(folders):
            raise AccountReadError('incomplete_folders', '账号收藏夹状态列表不完整')
        matches = [item for item in folders if item.id == collection_id and item.mid == self.connector.config.account_uid]
        if len(matches) != 1:
            raise AccountReadError('collection_unavailable', '未确认目标收藏夹属于配置账号')
        return bool(matches[0].fav_state)

    async def reconcile(self, call, resource_id):
        rows = await actions_for(self.store, account_uid=self.connector.config.account_uid, resource_id=resource_id)
        for row in rows:
            if row['status'] != 'unknown':
                continue
            capability = Capability(row['action_type'])
            # Revocation blocks even a new authenticated status read of that capability.
            await self.connector.job(call, capability)
            state = await self.current_state(resource_id, row['action_type'], row.get('collection_id'))
            await self.connector.job(call, capability)
            if state is not None and state == row['desired_state']:
                await record_outcome(self.store, row['action_id'], 'confirmed', 'desired_state_observed',
                    observed_state=state, reconciliation_job_id=call.job_id,
                    receipt_note='核对时平台状态已符合目标；不归因于未知请求，也不改写其原始尝试')
            else:
                raise AccountReadError('platform_outcome_unknown', '原平台动作结果仍未知；当前状态不能证明请求未执行，不重复写入')

    async def like(self, args, call):
        return await self.execute(args, call, Capability.BILIBILI_LIKE)

    async def favorite(self, args, call):
        return await self.execute(args, call, Capability.BILIBILI_FAVORITE)

    async def result(self, request):
        rows = await actions_for(self.store, job_id=request.job_id, scene_id=request.scene_id)
        fact = next(row for row in rows if row['action_id'] == request.action_id)
        return ToolResult(status='ok' if fact['status'] == 'confirmed' else 'error',
            error_code=None if fact['status'] == 'confirmed' else 'platform_' + fact['status'],
            content=json.dumps(fact, ensure_ascii=False), evidence_kind='external', coverage='platform_action_receipt',
            provenance=ObservationProvenance(access='account'),
            sources=[ToolSource(url='https://www.bilibili.com/video/av' + str(fact['resource_id']))])

    async def execute(self, args, call, capability):
        connector, config = self.connector, self.connector.config
        request = None
        attempted = False
        try:
            job = await connector.job(call, capability)
            if not self.available(call, capability):
                raise AccountReadError('capability_missing', '账号写动作缺少独立额度、CSRF 或设备 Cookie 配置')
            if isinstance(args, FavoriteInput) and args.collection_id not in config.allowed_collection_ids:
                raise AccountReadError('collection_denied', '收藏夹未在运营配置的允许列表中')
            parameters = {'account_uid': config.account_uid, **args.model_dump()}
            request = await connector.runtime.action_reviewer.request(job=job, native_call_id=call.tool_call_id,
                action_type=capability.value, target=f'bilibili:{config.account_uid}:av{args.resource_id}', parameters=parameters)
            lock = self.locks.setdefault((config.account_uid, args.resource_id), asyncio.Lock())
            async with lock:
                rows = await actions_for(self.store, account_uid=config.account_uid, resource_id=args.resource_id)
                own = next((row for row in rows if row['action_id'] == request.action_id), None)
                if own and own['status'] != 'unknown':
                    # Even a cancelled pre-wire call is not silently replayed.
                    return await self.result(request)
                if own is None:
                    await register_action(self.store, request, call.requester_qq_uid)
                await connector.runtime.action_reviewer.approve(request)
                await connector.job(call, capability)
                await connector.verify_identity()
                await connector.job(call, capability)
                await self.reconcile(call, args.resource_id)
                if own:
                    return await self.result(request)
                state = await self.current_state(args.resource_id, capability.value, getattr(args, 'collection_id', None))
                await connector.job(call, capability)
                if state is not None and state == args.desired_state:
                    await record_outcome(self.store, request.action_id, 'confirmed', 'already_in_desired_state',
                        observed_state=state, receipt_note='读取时已处于目标状态，本动作未发送写请求')
                    return await self.result(request)
                limit = config.daily_like_limit if capability == Capability.BILIBILI_LIKE else config.daily_favorite_limit
                await reserve_attempt(connector, call, request, capability, limit)
                attempted = True
                if capability == Capability.BILIBILI_LIKE:
                    endpoint = Endpoint.LIKE
                    form = {'aid': args.resource_id, 'like': 1 if args.desired_state else 2, 'csrf': config.bili_jct}
                else:
                    endpoint = Endpoint.FAVORITE
                    form = {'rid': args.resource_id, 'type': 2, 'csrf': config.bili_jct,
                            'add_media_ids' if args.desired_state else 'del_media_ids': str(args.collection_id)}
                response = await connector.request(endpoint, form=form, envelope=True)
                if response.code == 0:
                    await record_outcome(self.store, request.action_id, 'confirmed', 'platform_acknowledged', platform_code=0)
                elif response.code in {-101, -111, -400, -403, 10003, 65004, 65006, 2001000}:
                    await record_outcome(self.store, request.action_id, 'rejected', 'platform_rejected', platform_code=response.code)
                else:
                    await record_outcome(self.store, request.action_id, 'unknown', 'unrecognized_platform_response', platform_code=response.code)
                return await self.result(request)
        except asyncio.CancelledError:
            # Attempt occupancy is already durable before the first POST await.
            raise
        except (ValueError, PermissionError, OSError, httpx.HTTPError) as error:
            code = error.code if isinstance(error, AccountReadError) else type(error).__name__
            if request:
                rows = await actions_for(self.store, job_id=request.job_id, scene_id=request.scene_id)
                own = next((row for row in rows if row['action_id'] == request.action_id), None)
                if own:
                    if own['status'] == 'unknown' and not attempted:
                        # A repeated unknown call only queried state; retain its original attempt.
                        return await self.result(request)
                    status = ('not_sent' if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                              else 'unknown') if attempted else 'rejected'
                    if own['status'] not in {'confirmed', 'rejected'}:
                        await record_outcome(self.store, request.action_id, status, code)
                    return await self.result(request)
            result = ToolResult.failure(str(error) if isinstance(error, AccountReadError)
                else '账号动作未取得可执行结论', code)
            result.provenance = ObservationProvenance(access='account')
            return result
