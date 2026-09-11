from __future__ import annotations
import json, re, uuid
import httpx
from pydantic import BaseModel, ConfigDict, Field
from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext
from len_bot.media.models import MessageSegment
from len_bot.tools.results import ToolResult, ToolSource
from len_bot.tools.http import fetch_public
from len_bot.plugins.net_policy import validate_url
from ..bilibili_client import video_view, first_play_url

class ParseLinkArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1, pattern=r'https?://\S+')

class DownloadMediaArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    result_id: str = Field(min_length=1, description='parse_link 返回并已保存的资料 ID')
    media_index: int = Field(default=0, ge=0, le=8)

class LinkParserPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest); self.config = context.config; self.client = None
    async def on_load(self, context: PluginContext):
        self.client = httpx.AsyncClient(timeout=self.config.request_timeout_seconds, trust_env=False,
                                        headers={'User-Agent':'LenBot link parser'}, follow_redirects=False)
        context.register_tool('parse_link', '解析 B 站视频链接的标题、作者、简介和规范链接；只读取资料，不自动发送。',
            ParseLinkArguments, self.parse_link, purpose='解析 B 站链接', aliases=('解析链接','B站链接'),
            keywords=('B站','哔哩哔哩','链接','视频'), kind='read', roles=('conversation','work'), deferred=False)
        context.register_tool('download_media', '下载已确认的 B 站视频或音频公开地址并保存为场景媒体；不会自动发送。',
            DownloadMediaArguments, self.download_media, purpose='下载 B 站媒体', aliases=('下载视频','下载音频'),
            keywords=('B站','视频','音频','下载','媒体'), kind='read', roles=('conversation','work'), deferred=True)
        context.register_handler(id='parse_bilibili_link', description='识别纯 B 站链接或“解析链接 URL”命令并读取公开资料',
            match=self.match_link, handler=self.on_link, priority=40, consume=True)
    async def on_unload(self):
        if self.client: await self.client.aclose(); self.client = None
    def match_link(self, call):
        if 'bilibili' not in self.config.enabled_platforms:
            return False
        text = call.event.raw_text.strip()
        return self._extract_url(text) is not None

    @staticmethod
    def _extract_url(text: str) -> str | None:
        if re.fullmatch(r'https?://(?:www\.)?bilibili\.com/video/[A-Za-z0-9/?=&._-]+', text):
            return text
        match = re.fullmatch(r'(?:解析链接|解析B站)\s+(https?://\S+)', text)
        return match.group(1) if match else None
    async def parse_link(self, args: ParseLinkArguments, call_context: PluginCallContext) -> ToolResult:
        if 'bilibili' not in self.config.enabled_platforms:
            return ToolResult(status='unsupported', content='B站解析适配器在当前插件配置中未启用', error_code='platform_disabled')
        allowed, reason = validate_url(args.url)
        if not allowed: return ToolResult.failure(f'安全拦截: {reason}', 'blocked')
        match = re.search(r'/video/(BV[A-Za-z0-9]{10}|av\d+)', args.url)
        if not match: return ToolResult.failure('暂只支持 B 站视频 BV/av 链接', 'unsupported')
        ident = match.group(1); params = {'bvid': ident} if ident.startswith('BV') else {'aid': ident[2:]}
        source = ToolSource(url='https://api.bilibili.com/x/web-interface/view')
        try:
            item = await video_view(self.client, bvid=params.get('bvid'), aid=int(params['aid']) if 'aid' in params else None)
        except Exception as error:
            return ToolResult.failure(f'链接解析失败：{type(error).__name__}', 'request_failed')
        resource=item.get('bvid') or str(item.get('aid'))
        cid = ((item.get('pages') or [{}])[0] or {}).get('cid')
        result={'platform':'bilibili','resource_id':resource,'canonical_url':f'https://www.bilibili.com/video/{resource}',
                'title':item.get('title',''),'author':item.get('owner',{}).get('name',''),'text':item.get('desc',''),
                'published_at':item.get('pubdate'),'media':{'available':False,'items':[],'coverage':'metadata_only'}}
        # Playback URLs are a separate download concern. Parsing metadata must
        # not silently fetch a second, potentially signed media endpoint.
        result['download'] = {'resource_id': resource, 'cid': cid, 'type': 'video'}
        return ToolResult(content=json.dumps(result,ensure_ascii=False), sources=[source], evidence_kind='external', coverage='metadata_only')

    async def download_media(self, args: DownloadMediaArguments, call_context: PluginCallContext) -> ToolResult:
        runtime = call_context.plugin._runtime
        stored = await runtime.event_store.read_tool_observation(args.result_id, [call_context.scene_id])
        if stored is None or stored.tool_name != 'parse_link':
            return ToolResult.failure('只能下载本场景已保存的 parse_link 结果', 'invalid_source')
        try:
            parsed = json.loads(stored.content)
        except (TypeError, ValueError):
            return ToolResult.failure('parse_link 结果不是有效的保存资料', 'invalid_source')
        media_items = parsed.get('media', {}).get('items', [])
        try:
            media = media_items[args.media_index]
            url, media_type = media['url'], media['type']
        except (TypeError, KeyError, IndexError):
            download = parsed.get('download') or {}
            if args.media_index != 0 or not download.get('resource_id') or not download.get('cid'):
                return ToolResult.failure('parse_link 结果中没有可下载的媒体项', 'media_unavailable')
            media_type = download.get('type', 'video')
            try:
                url = await first_play_url(self.client, download['resource_id'], int(download['cid']))
            except Exception as error:
                return ToolResult.failure(f'播放地址读取失败：{type(error).__name__}', 'playback_unavailable')
        if media_type not in {'video', 'audio'} or not isinstance(url, str):
            return ToolResult.failure('parse_link 媒体项类型或地址无效', 'invalid_source')
        allowed, reason = validate_url(url)
        if not allowed: return ToolResult.failure(f'安全拦截: {reason}', 'blocked')
        try:
            cached = await runtime.event_store.media_by_locator(call_context.scene_id, url)
            if cached:
                try:
                    cached_asset, cached_data = await runtime.media_service.get_file_bytes(cached['id'], call_context.scene_id)
                    if not (cached_asset.get('mime_type') or '').startswith(media_type + '/'):
                        raise ValueError('缓存媒体类型与本次提案不一致')
                    return ToolResult(content=json.dumps({'asset_id':cached_asset['id'],'type':media_type,'mime_type':cached_asset.get('mime_type'),'bytes':len(cached_data),'coverage':'downloaded_media','cached':True},ensure_ascii=False), attachments=[cached_asset['id']], sources=[ToolSource(url=url)], evidence_kind='external', coverage='downloaded_media', cached=True)
                except Exception:
                    pass
            final_url, headers, data = await fetch_public(self.client, url, max_bytes=runtime.config.media_max_file_bytes)
            mime = headers.get('content-type', '').split(';',1)[0].lower()
            asset_id = 'media_' + uuid.uuid4().hex
            asset = await runtime.media_service.save_downloaded(
                asset_id, call_context.scene_id, final_url, data, mime, 'B站链接下载媒体',
                source_event_id=call_context.source_event_id, expected_type=media_type)
            return ToolResult(content=json.dumps({'asset_id':asset['id'],'type':'video' if mime.startswith('video/') else 'audio','mime_type':mime,'bytes':len(data),'coverage':'downloaded_media'},ensure_ascii=False), attachments=[asset['id']], sources=[ToolSource(url=final_url)], evidence_kind='external', coverage='downloaded_media')
        except Exception as error:
            return ToolResult.failure(f'媒体下载失败：{type(error).__name__}', 'download_failed')
    async def on_link(self, call: PluginCallContext):
        url = self._extract_url(call.event.raw_text.strip())
        if url is None:
            raise ValueError('未找到可解析的 B 站视频链接')
        observed = await call.invoke_tool('parse_link', ParseLinkArguments(url=url))
        if observed.status not in {'ok','partial'}: raise ValueError(observed.content)
        parsed = json.loads(observed.content)
        media_count = len((parsed.get('media') or {}).get('items') or [])
        text = f"{parsed.get('title') or 'B站视频'}\nUP主：{parsed.get('author') or '未提供'}\n{parsed.get('canonical_url', '')}"
        if parsed.get('text'):
            text += f"\n简介：{parsed['text'][:500]}"
        if media_count:
            text += f"\n可用媒体：{media_count} 项（需由 Agent 明确下载与发送）"
        await call.submit_message([MessageSegment(type='text', text=text)])
