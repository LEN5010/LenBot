"""Persistent, scoped exports; upload permission never follows a host pathname."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import stat
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from len_bot.cognition.action_review import ActionRequest, bind_allowed, load_review, may_execute
from len_bot.media.service import validate_image
from len_bot.runtime.capabilities import Capability, CapabilitySubject

MAX_FILE_BYTES = 50_000_000
FORMATS = {'txt': 'text/plain', 'md': 'text/markdown', 'csv': 'text/csv', 'json': 'application/json',
           'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg',
           'jpeg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif', 'zip': 'application/zip'}


class PrepareFileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str = Field(min_length=1, max_length=240)
    execution_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=160,
        description='真实格式的展示名；支持 TXT/MD/CSV/JSON/PDF/PNG/JPEG/WEBP/GIF/ZIP，MD 按 UTF-8 纯文本检查；不支持独立脚本、可执行文件或 Office')
    for_upload: bool = Field(default=False, description='为本群上传取得独立 send_file 授权和动作审查；不会立即上传')

    @field_validator('display_name')
    @classmethod
    def simple_name(cls, value):
        if value in {'.', '..'} or any(c in value for c in '/\\\x00\r\n') or any(ord(c) < 32 for c in value):
            raise ValueError('展示名必须是单个普通文件名')
        return value


class FileAsset(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    asset_id: str
    scene_id: str
    job_id: str
    job_revision: int
    requester_qq_uid: str
    execution_id: str
    artifact_id: str
    source_path: str
    display_name: str
    size_bytes: int
    mime_type: str
    created_at: float
    expires_at: float
    review_action_id: str | None = None


def file_receipt_status(receipt):
    """A saved file receipt is success only with a real platform file identity."""
    if receipt.get('simulated') or receipt.get('origin_mode') == 'simulated':
        return 'simulated'
    if receipt.get('event_type') == 'ACTION_SHADOWED' or receipt.get('origin_mode') == 'shadow':
        return 'shadow'
    if receipt.get('delivery_unknown') or receipt.get('delivery_status') == 'unknown':
        return 'unknown'
    if receipt.get('event_type') == 'FILE_UPLOADED':
        file_id = receipt.get('file_id')
        return ('uploaded' if isinstance(file_id, str) and file_id.strip()
                and receipt.get('delivery_status') in {None, 'sent'} else 'unknown')
    if receipt.get('event_type') == 'FILE_UPLOAD_FAILED' and receipt.get('delivery_status') in {'not_sent', 'rejected'}:
        return receipt['delivery_status']
    return 'unknown'


def file_upload_state(record):
    """Project existing submission/attempt/receipt identities; never enqueue."""
    actions = {}
    unlinked_submissions = 0
    for submission in record.get('upload_submissions', []):
        if submission['action_id']:
            actions.setdefault(submission['action_id'], {'status': 'submitted'})
        else:
            unlinked_submissions += 1
    unlinked_attempts = 0
    for attempt in record.get('upload_attempts', []):
        if attempt.get('action_id'):
            actions[attempt['action_id']] = {'status': 'unknown'}
        else:
            unlinked_attempts += 1
    # Receipts are stored newest first. A failure of a different action must
    # not close a later attempt that has not received its own receipt.
    closed = set()
    unlinked = []
    for receipt in record.get('upload_receipts', []):
        status = file_receipt_status(receipt)
        action_id = receipt.get('action_id')
        if not action_id:
            unlinked.append(status)
        elif action_id not in closed:
            actions[action_id] = {'status': status, 'receipt_event_id': receipt['event_id'],
                                  'file_id': receipt.get('file_id') if status == 'uploaded' else None}
            closed.add(action_id)
    states = [item['status'] for item in actions.values()] + unlinked
    uploaded = any(file_receipt_status(receipt) == 'uploaded' for receipt in record.get('upload_receipts', []))
    unknown = 'unknown' in states or bool(unlinked_attempts)
    submitted = 'submitted' in states or bool(unlinked_submissions)
    status = ('unknown' if unknown else 'uploaded' if uploaded else 'submitted' if submitted else
              'failed' if any(value in {'not_sent', 'rejected'} for value in states) else
              'shadow' if 'shadow' in states else 'simulated' if 'simulated' in states else 'prepared')
    return {'status': status, 'uploaded': uploaded, 'unknown': unknown, 'submitted': submitted,
            'actions': [{'action_id': ident, **value} for ident, value in actions.items()],
            'unlinked_submissions': unlinked_submissions,
            'unlinked_attempts': unlinked_attempts,
            'unlinked_receipts': len(unlinked)}


def public_file_candidate(record):
    """Model-visible file handle; never includes host paths or bytes."""
    state = record['upload_state']
    return {
        'file_asset_id': record['asset_id'],
        'display_name': record['display_name'],
        'size_bytes': record['size_bytes'],
        'mime_type': record['mime_type'],
        'job_id': record['job_id'],
        'job_revision': record['job_revision'],
        'execution_id': record.get('execution_id'),
        'expires_at': record['expires_at'],
        'expired': bool(record.get('expired')),
        'reviewed_for_upload': bool(record.get('review_action_id')),
        'uploaded': state['uploaded'],
        'upload_unknown': state['unknown'],
        'upload_pending': state['submitted'],
        'current_revision': record['current_revision'],
        'delivery_status': state['status'],
    }


def file_delivery_facts(runtime, scene_id=None, requester=None):
    """Read-only projection of generate / prepare / upload conditions."""
    from len_bot.plugins.builtin.workspace.config import configured_workspace
    root = runtime.config_store.current
    delivery = runtime.config.file_delivery
    upload = runtime.config.onebot_file_upload
    workspace, selection_error = None, None
    try:
        workspace = configured_workspace(root)
    except ValueError as error:
        selection_error = str(error)
    enabled_globally = [workspace[0]] if workspace and root.plugins[workspace[0]].enabled else []
    scene = root.scenes.get(scene_id) if scene_id else None
    selected = [name for name in enabled_globally if not scene_id or
                scene and scene.enabled and name in scene.plugins and scene.plugins[name].enabled]
    backends = [{'plugin_id': name, 'backend': 'gateway' if root.plugins[name].parsed_config.gateway else 'worker'}
                for name in selected]
    can_generate = bool(selected)
    can_prepare_asset = any(item['backend'] == 'gateway' for item in backends)
    blocked = [selection_error] if selection_error else []
    if not can_generate:
        blocked.append('本群未开放可生成文件的工作空间')
    elif not can_prepare_asset:
        blocked.append('本机 worker 可生成普通产物，但持久文件资产登记只支持已确认的 Gateway 产物')
    if not delivery.enabled:
        blocked.append('runtime.file_delivery.enabled=false，仅可在工作面板下载')
    if upload is None:
        blocked.append('onebot_file_upload 未配置')
    elif not upload.deployment_verified:
        blocked.append(f'onebot_file_upload.deployment_verified=false（{upload.implementation} 的文件动作与只读挂载尚未人工核对）')
    grant_allowed = None
    if scene_id and not scene_id.startswith('group:'):
        blocked.append('普通文件上传只接受目标群')
    elif not scene_id or not requester:
        blocked.append('尚未选择目标群与真实申请者，上传资格未核对')
    else:
        authority = runtime.runtime_gate.capability_authority
        if authority is None:
            grant_allowed = False
            blocked.append('当前运行时没有文件上传授予检查')
        else:
            decision = authority.check(Capability.SEND_FILE, CapabilitySubject('human', requester, scene_id, None),
                                       now=runtime.clock())
            grant_allowed = decision.allowed
            if not decision.allowed:
                blocked.append(decision.reason)
    platform_ready = bool(delivery.enabled and upload and upload.deployment_verified)
    can_upload = (False if not platform_ready or scene_id and not scene_id.startswith('group:') else grant_allowed)
    return {
        'can_generate': can_generate,
        'can_prepare_asset': can_prepare_asset,
        'can_upload_to_target': can_upload,
        'platform_configured': platform_ready,
        'workspace_backends': backends,
        'meaning': '工作空间来自已保存配置；上传协议与文件交付开关来自运行时。这里只核对所选申请者的授予，不探测部署连接，也不替代工作版本、资产、审查、额度与上传回执',
        'blocked_reason': '；'.join(blocked) if blocked else None,
        'file_delivery_enabled': delivery.enabled,
        'implementation': None if upload is None else upload.implementation,
        'protocol': None if upload is None else upload.protocol,
        'deployment_verified': None if upload is None else upload.deployment_verified,
    }


def inspect_zip(data: bytes, *, depth=0, totals=None):
    if depth > 3:
        raise ValueError('ZIP 嵌套超过 3 层')
    totals = totals if totals is not None else [0, 0]
    sensitive = {'lenbot.config.json', 'gateway.config.json', '.env', 'id_rsa', 'id_ed25519'}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        seen = set()
        for entry in archive.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            if (not name or '\\' in name or '\x00' in name or ':' in name or path.is_absolute()
                    or '..' in path.parts or name in seen):
                raise ValueError('ZIP 包含重复或非相对普通路径')
            seen.add(name)
            mode = entry.external_attr >> 16
            if entry.flag_bits & 1 or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError('ZIP 包含加密条目、链接或特殊文件')
            if any(p.lower() in sensitive or p.lower().endswith(('.sqlite', '.sqlite3', '.db', '.pem', '.key'))
                   or p.lower() in {'lenbot-control', '.ssh', '.git'} for p in path.parts):
                raise ValueError('ZIP 含不可导出的控制、凭据或数据库名称')
            if entry.is_dir():
                continue
            totals[0] += 1
            totals[1] += entry.file_size
            if totals[0] > 1000 or totals[1] > 100_000_000:
                raise ValueError('ZIP 展开超过 1000 文件或 100MB')
            with archive.open(entry) as source:
                content = source.read(min(entry.file_size + 1, 100_000_001))
            if len(content) != entry.file_size:
                raise ValueError('ZIP 条目实际大小不一致')
            if content.startswith((b'PK\x03\x04', b'PK\x05\x06')) or path.suffix.lower() == '.zip':
                inspect_zip(content, depth=depth + 1, totals=totals)
            elif path.suffix.lower() in {'.tar', '.gz', '.bz2', '.xz', '.rar', '.7z'} or content.startswith((b'7z\xbc\xaf\x27\x1c', b'Rar!', b'\x1f\x8b', b'BZh', b'\xfd7zXZ')):
                raise ValueError('ZIP 内含当前不能检查的嵌套压缩格式')


def inspect_file(data, name, *, max_bytes, max_pixels):
    if not data or len(data) > max_bytes:
        raise ValueError('文件为空或超过当前上限（最多 50MB）')
    suffix = Path(name).suffix.lower().removeprefix('.')
    mime = FORMATS.get(suffix)
    if mime is None:
        raise ValueError('unsupported：仅支持 TXT/MD/CSV/JSON/PDF/PNG/JPEG/WEBP/GIF/ZIP')
    if mime.startswith('image/'):
        if validate_image(data, max_bytes=max_bytes, max_pixels=max_pixels) != mime:
            raise ValueError('展示名与实际图片格式不一致')
    elif suffix == 'zip':
        inspect_zip(data)
    elif suffix == 'pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
            raise ValueError('文件不是有完整边界的 PDF')
    else:
        text = data.decode('utf-8-sig')
        if '\x00' in text or any(ord(c) < 32 and c not in '\t\n\r' for c in text):
            raise ValueError('文本文件含二进制控制字节')
        if suffix == 'json':
            json.loads(text)
        elif suffix == 'csv':
            for _ in csv.reader(io.StringIO(text), strict=True):
                pass
    return mime


async def initialize_files(store):
    await store._db.execute('''CREATE TABLE IF NOT EXISTS file_assets (
        asset_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, job_id TEXT NOT NULL,
        job_revision INTEGER NOT NULL, artifact_id TEXT NOT NULL, asset_json TEXT NOT NULL,
        UNIQUE(scene_id,job_id,job_revision,artifact_id))''')


async def load_file(store, asset_id, scene_id):
    row = await (await store._db.execute('SELECT asset_json FROM file_assets WHERE asset_id=? AND scene_id=?',
        (asset_id, scene_id))).fetchone()
    return FileAsset.model_validate_json(row[0]) if row else None


def file_permission(store, scene_id, requester):
    authority = store.capability_authority
    if authority is None or not requester or not scene_id.startswith('group:'):
        raise ValueError('文件上传需要原工作真实用户和目标群')
    config = authority.config_store.current.runtime.file_delivery
    if not config.enabled:
        raise ValueError('普通文件交付未启用；文件仍可在授权面板下载')
    decision = authority.check(Capability.SEND_FILE, CapabilitySubject('human', requester, scene_id, None), now=store.clock())
    if not decision.allowed:
        raise ValueError(decision.reason)
    return config


async def validate_file_action(store, action, *, quota=False):
    config = file_permission(store, action.scene_id, action.requester_qq_uid)
    asset = await load_file(store, action.file_asset_id, action.scene_id)
    if (asset is None or asset.job_id != action.job_id or asset.job_revision != action.job_revision
            or asset.requester_qq_uid != action.requester_qq_uid or asset.expires_at <= store.clock()
            or asset.size_bytes > config.max_file_bytes or not asset.review_action_id):
        raise ValueError('文件资产不属于当前工作/请求者，或已过期、超限、未审查')
    job = await store.get_job(asset.job_id, asset.scene_id)
    if not job or job['revision'] != asset.job_revision or job['status'] in {'cancelled', 'failed', 'delivery_unknown'}:
        raise ValueError('原工作修订已失效或交付结果未知')
    decision = bind_allowed(await load_review(store, asset.review_action_id, asset.job_revision),
        asset.review_action_id, asset.job_revision, 'send_file')
    if not may_execute(decision):
        raise ValueError(decision.public_summary or '文件动作缺少有效审查')
    row = await (await store._db.execute("""SELECT payload FROM events WHERE event_type='ACTION_REQUESTED'
        AND scene_id=? AND json_extract(payload,'$.action_id')=?""", (asset.scene_id, asset.review_action_id))).fetchone()
    request = ActionRequest.model_validate_json(row[0]) if row else None
    if (request is None or request.target != asset.scene_id or request.job_id != asset.job_id
            or request.job_revision != asset.job_revision or request.action_type != 'send_file'
            or request.parameters != asset.model_dump(exclude={'review_action_id'})):
        raise ValueError('文件审查参数与持久资产不一致')
    if quota:
        settings = store.capability_authority.config_store.current.time
        if settings is None:
            raise ValueError('文件发送额度需要明确业务时区')
        day = datetime.fromtimestamp(store.clock(), ZoneInfo(settings.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await (await store._db.execute("""SELECT payload,timestamp FROM events WHERE scene_id=?
            AND event_type='DELIVERY_ATTEMPTED' AND json_extract(payload,'$.file_asset_id') IS NOT NULL""",
            (action.scene_id,))).fetchall()
        used = 0
        for raw, at in rows:
            old = json.loads(raw)
            if old['action_id'] == action.id:
                continue
            fact = await store.delivery_fact(old['action_id'], action.scene_id)
            if fact and fact[0] not in {'sent', 'unknown'}:
                continue
            if old['file_asset_id'] == asset.asset_id:
                raise ValueError('该文件已上传或上传结果未知，不能重复上传')
            used += day.timestamp() <= at < (day + timedelta(days=1)).timestamp()
        if used >= config.daily_group_limit:
            raise ValueError('本群今日文件上传额度已用完（最多 10 个）')
    return asset


class FileAssetService:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.event_store
        self.root = Path(runtime.config.db_path).resolve().parent / 'file_assets'
        self._lock = asyncio.Lock()

    def path(self, asset):
        # Only database-generated names; no model path is accepted here.
        return self.root / asset.asset_id

    async def prepare(self, service, call, values):
        from len_bot.execution.service import GatewayWorkspaceService
        from len_bot.execution.protocol import ExecutionState
        if not isinstance(service, GatewayWorkspaceService):
            raise ValueError('持久文件资产只接受 Gateway 已确认的不可变产物')
        await self.runtime.plugin_host.validate_call(call)
        scope = await service.scope_for(call)
        job = await service._require_current_admission(scope.job_id, scope.scene_id, call.job_revision)
        if not job['requester_qq_uid']:
            raise ValueError('自主公共研究没有文件导出或上传入口')
        config = self.runtime.config.file_delivery
        async with self._lock:
            await self.runtime.plugin_host.validate_call(call)
            job = await service._require_current_admission(scope.job_id, scope.scene_id, call.job_revision)
            row = await service._artifact_by_path(scope.scene_id, scope.job_id, values.path, execution_id=values.execution_id)
            record = await self.store.get_execution(values.execution_id)
            if (row is None or record is None or record.job_revision != call.job_revision or record.worker_type != 'python'
                    or record.state not in {ExecutionState.EXITED, ExecutionState.FAILED, ExecutionState.TERMINATION_CONFIRMED}):
                raise ValueError('文件不是当前工作修订的 Python 产物')
            if row['size_bytes'] > config.max_file_bytes:
                raise ValueError('文件超过当前大小上限')
            existing = await (await self.store._db.execute('''SELECT asset_json FROM file_assets
                WHERE scene_id=? AND job_id=? AND job_revision=? AND artifact_id=?''',
                (scope.scene_id, scope.job_id, job['revision'], row['artifact_id']))).fetchone()
            if existing:
                asset = FileAsset.model_validate_json(existing[0])
                if asset.display_name != values.display_name or asset.expires_at <= self.store.clock():
                    raise ValueError('同一不可变产物的展示名不能改变，过期产物需重新生成')
            else:
                buffer = bytearray()
                async for chunk in service.client.artifact_chunks(row['artifact_id']):
                    buffer.extend(chunk)
                    if len(buffer) > config.max_file_bytes:
                        raise ValueError('实际文件字节超过上限')
                if len(buffer) != row['size_bytes']:
                    raise ValueError('实际产物大小与登记不一致')
                data = bytes(buffer)
                mime = await asyncio.to_thread(inspect_file, data, values.display_name,
                    max_bytes=config.max_file_bytes, max_pixels=self.runtime.config.media_max_image_pixels)
                await service._require_current_admission(scope.job_id, scope.scene_id, call.job_revision)
                now = self.store.clock()
                asset = FileAsset(asset_id='file_' + uuid.uuid4().hex, scene_id=scope.scene_id,
                    job_id=scope.job_id, job_revision=job['revision'], requester_qq_uid=job['requester_qq_uid'],
                    execution_id=values.execution_id, artifact_id=row['artifact_id'], source_path=values.path,
                    display_name=values.display_name, size_bytes=len(data), mime_type=mime,
                    created_at=now, expires_at=now + config.retention_seconds)
                self.root.mkdir(mode=0o750, parents=True, exist_ok=True)
                with self.path(asset).open('xb') as output:
                    output.write(data)
                self.path(asset).chmod(0o440)
                async with self.store._write_lock:
                    try:
                        await service._require_current_admission(scope.job_id, scope.scene_id, call.job_revision)
                        await self.store._db.execute('INSERT INTO file_assets VALUES (?,?,?,?,?,?)',
                            (asset.asset_id, asset.scene_id, asset.job_id, asset.job_revision, asset.artifact_id, asset.model_dump_json()))
                        await self.store._db.commit()
                    except BaseException:
                        await self.store._db.rollback()
                        self.path(asset).unlink(missing_ok=True)
                        raise
            if values.for_upload:
                file_permission(self.store, asset.scene_id, asset.requester_qq_uid)
                reviewer = self.runtime.action_reviewer
                request = await reviewer.request(job=job, native_call_id=call.tool_call_id, action_type='send_file',
                    target=asset.scene_id, parameters=asset.model_dump(exclude={'review_action_id'}))
                await reviewer.approve(request)
                await self.runtime.plugin_host.validate_call(call)
                await service._require_current_admission(scope.job_id, scope.scene_id, call.job_revision)
                file_permission(self.store, asset.scene_id, asset.requester_qq_uid)
                asset.review_action_id = request.action_id
                async with self.store._write_lock:
                    await self.store._db.execute('UPDATE file_assets SET asset_json=? WHERE asset_id=?',
                        (asset.model_dump_json(), asset.asset_id))
                    await self.store._db.commit()
            return {'file_asset': asset.model_dump(), 'uploaded': False,
                'note': '资产已保存。上传需对话 respond 使用 file_asset_id 和原工作交付引用；以独立文件回执为准。'}

    async def for_job(self, scene_id, job_id):
        job = await self.store.get_job(job_id, scene_id)
        sampled_at = self.store.clock()
        rows = await (await self.store._db.execute('SELECT asset_json FROM file_assets WHERE scene_id=? AND job_id=?',
            (scene_id, job_id))).fetchall()
        result = []
        for row in rows:
            asset = FileAsset.model_validate_json(row[0])
            receipts = await (await self.store._db.execute("""SELECT id,event_type,payload,timestamp,metadata FROM events
                WHERE scene_id=? AND json_extract(payload,'$.file_asset_id')=?
                  AND event_type IN ('FILE_UPLOADED','FILE_UPLOAD_FAILED','ACTION_SHADOWED') ORDER BY rowid DESC""",
                (scene_id, asset.asset_id))).fetchall()
            attempts = await (await self.store._db.execute("""SELECT id,payload,timestamp FROM events WHERE scene_id=?
                AND event_type='DELIVERY_ATTEMPTED' AND json_extract(payload,'$.file_asset_id')=? ORDER BY rowid""",
                (scene_id, asset.asset_id))).fetchall()
            submissions = await (await self.store._db.execute('''SELECT e.id,e.timestamp,
                json_extract(e.payload,'$.action_ids[' || m.key || ']'),json_extract(m.value,'$.job_revision')
                FROM events e,json_each(e.payload,'$.outcome.message_proposals') m
                WHERE e.scene_id=? AND e.event_type='CONVERSATION_COMMITTED'
                AND json_extract(m.value,'$.file_asset_id')=? ORDER BY e.rowid,m.key''',
                (scene_id, asset.asset_id))).fetchall()
            reviews = (await self.store.query_traces(scene_id=scene_id, kind='action_review',
                ref_id=f'{asset.review_action_id}:{asset.job_revision}', limit=1)) if asset.review_action_id else []
            item = {**asset.model_dump(), 'expired': asset.expires_at <= sampled_at,
                'current_revision': bool(job and asset.job_revision == job['revision']), 'sampled_at': sampled_at,
                'review_trace_id': reviews[0]['id'] if reviews else None,
                'upload_submissions': [{'event_id': row[0], 'timestamp': row[1], 'action_id': row[2],
                                        'job_revision': row[3]} for row in submissions],
                'upload_attempts': [{**json.loads(row[1]), 'event_id': row[0], 'timestamp': row[2]} for row in attempts],
                'upload_receipts': [{**json.loads(row[2]), 'event_id': row[0], 'event_type': row[1],
                    'timestamp': row[3], 'simulated': bool(json.loads(row[4]).get('simulated'))} for row in receipts]}
            for receipt in item['upload_receipts']:
                receipt['file_status'] = file_receipt_status(receipt)
            item['upload_state'] = file_upload_state(item)
            result.append(item)
        return result

    async def prepare_action(self, action):
        asset = await validate_file_action(self.store, action)
        settings = self.runtime.config.onebot_file_upload
        if settings is None or not settings.deployment_verified:
            raise ValueError('capability_missing：尚未核对实际 OneBot 文件协议与只读挂载；资产仍可下载')
        info = self.path(asset).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != asset.size_bytes:
            raise ValueError('文件资产不再是已登记大小的普通文件')
        return action.model_copy(update={'resolved_file': str(PurePosixPath(settings.export_mount_path) / asset.asset_id),
                                         'file_name': asset.display_name})

    async def bytes_for_job(self, scene_id, job_id, asset_id):
        asset = await load_file(self.store, asset_id, scene_id)
        if asset is None or asset.job_id != job_id:
            raise ValueError('文件资产不属于此工作')
        data = await asyncio.to_thread(self.path(asset).read_bytes)
        if len(data) != asset.size_bytes:
            raise ValueError('持久文件资产大小已改变')
        return data, asset
