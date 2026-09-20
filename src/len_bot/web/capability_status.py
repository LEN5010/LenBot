"""Read-only operator cards over existing configuration, admission and receipts.

The catalogue below is display grouping, never another permission registry.
Execution still uses PluginHost, ScenePolicy and CapabilityAuthority.
"""
import json
from dataclasses import asdict

from len_bot.actions.models import receipt_delivery_status
from len_bot.plugins.models import PluginCallContext
from len_bot.runtime.capabilities import Capability, CapabilitySubject


# Display grouping bound to catalog plugin IDs. Missing owners stay visible;
# they are never silently dropped and then treated as a complete card.
CARDS = (
    ('web', '网页与公开资料', (
        ('web_search_tool', ('web_search', 'read_page')),
        ('link_parser', ('parse_link',)),
        ('bilibili_content', ('search_bilibili', 'get_video_info', 'get_video_pages',
                              'get_video_comments', 'get_video_subtitles')),
    ), ('web_search', 'read_page'), '直接对话或委托工作'),
    ('python', 'Python 与文件分析', (
        ('workspace', ('run_python', 'export_workspace_artifact')),
        ('python_workspace', ('run_python', 'export_workspace_artifact')),
    ), ('run_python', 'export_workspace_artifact'), '明确委托的信息工作'),
    ('browser', '浏览器', (
        ('browser_agent', ('browser_open', 'browser_capture', 'browser_snapshot')),
    ), ('browser_open', 'browser_capture'), '委托工作；按当前后端与工具限制执行'),
    ('broadcast', '直播日历与播报', (
        ('asoul_calendar', ()),
        ('asoul_dynamics', ()),
        ('bilibili_live_sensor', ()),
    ), (), '群命令、订阅与来源事件'),
    ('reports', '群报告', (('group_summary', ()),), (), '本群委托工作'),
    ('research', '公共研究与分享', (('interest_share', ()),), (), '系统研究周期；各群独立分享机会'),
    ('media', '媒体片段', (
        ('media_analysis', ('get_video_segment', 'transcribe_video_segment')),
    ), ('get_video_segment', 'transcribe_video_segment'), '委托工作'),
    ('files', '文件交付', (
        ('workspace', ('prepare_workspace_file',)),
        ('python_workspace', ('prepare_workspace_file',)),
    ), ('prepare_workspace_file',), '工作成果的独立文件行动'),
    ('account', '账号操作', (
        ('bilibili_content', ('get_dynamic_feed', 'set_bilibili_like', 'set_bilibili_favorite')),
    ), (), '另获授权的工作与逐动作审查'),
    ('core', '可选 Core', (('gscore_adapter', ()),), (), '已配置的专用适配入口'),
)


async def capability_status(query, scene_id=None, requester=None):
    from len_bot.plugins.builtin.workspace.config import configured_workspace
    rt = query.runtime
    root = rt.config_store.current
    now = query.current_time()
    plugins = {item['id']: item for item in query.plugins()}
    call = (PluginCallContext(scene_id=scene_id, requester_qq_uid=requester, now=now,
            cutoff_rowid=0, episode_id=None, job_id=None, role='conversation') if scene_id else None)
    availability = {item['plugin_id']: item for item in rt.plugin_host.capability_facts(call)} if call else {}
    authority = rt.runtime_gate.capability_authority
    workspace, workspace_error = None, None
    try:
        workspace = configured_workspace(root)
    except ValueError as error:
        workspace_error = str(error)
    workspace_config = workspace[1] if workspace else None
    backend = ('gateway' if workspace_config and workspace_config.gateway else
               'local' if workspace_config and workspace_config.worker else None)
    items = []
    catalog = rt.config_store.catalog.entries
    requirement_labels = {
        'unconfigured':'插件参数未配置','plugin_disabled':'插件未启用或尚未装载',
        'scene_not_enabled':'当前场景或申请者未开放','capability_denied':'当前授予检查未通过',
        'current_work_required':'需要真实工作上下文','human_requester_required':'需要明确的人类申请者',
        'role_not_supported':'当前职责不包含该工具','proposal_entry_required':'需要原提案入口',
        'entry_conditions_not_met':'需要在工具原入口核对额外条件',
        'jobs_disabled':'后台工作开关关闭','work_model_unconfigured':'尚未配置工作模型',
        'invalid_system_source':'系统来源不符合当前入口',
    }
    for ident, title, implementations, evidence_tools, entry in CARDS:
        item = {'id': ident, 'title': title, 'entry': entry, 'plugins': [],
                'actions': [], 'missing_owners': [],
                'deployment': [], 'authorization': [], 'recent_observation': None,
                'recent_execution': None, 'recent_delivery': None, 'recent_platform_action': None}
        declared = []
        for owner, owner_actions in implementations:
            fact = plugins.get(owner)
            if fact is None:
                entry_spec = catalog.get(owner)
                item['missing_owners'].append(owner)
                item['plugins'].append({
                    'id': owner, 'name': entry_spec.spec.name if entry_spec else owner,
                    'configured': False, 'enabled': False, 'active_enabled': False,
                    'state': 'absent', 'config_apply': None, 'last_error': '能力卡绑定了未装入 catalog 的插件 ID',
                    'scene_open': None, 'request_status': None, 'source_status': None,
                    'actions': [{'name': name, 'purpose': '插件未装入，无法核对该动作', 'roles': []}
                                for name in owner_actions]})
                continue
            current = availability.get(fact['id'])
            tools = {tool['name']: tool for tool in fact['tools']}
            shown = []
            for name in owner_actions:
                tool = tools.get(name)
                if tool:
                    shown.append({'name': tool['name'], 'purpose': tool['purpose'], 'roles': tool['roles']})
                else:
                    shown.append({'name': name, 'purpose': '已声明但当前未注册', 'roles': []})
            declared.extend(tool['name'] for tool in shown if tool['name'] in tools)
            row = {key: fact[key] for key in
                   ('id', 'name', 'configured', 'enabled', 'active_enabled', 'state', 'config_apply', 'last_error')}
            row.update(
                scene_open=rt.scene_policy.plugin_allowed(scene_id, fact['id'], 'conversation') if scene_id else None,
                request_status=current['status'] if current else None,
                source_status=fact['source_status'],
                actions=shown)
            item['plugins'].append(row)
            if current:
                if current['purposes']:
                    item['deployment'].append({'label':f"{fact['name']} · 对话入口用途",'value':'、'.join(current['purposes'])})
                work_purposes=current.get('delegable_purposes',[])
                if work_purposes:
                    item['deployment'].append({'label':f"{fact['name']} · 工作用途",'value':'、'.join(work_purposes)+'；创建工作后仍核对来源与额度'})
                for conditional in current.get('conditional_work_purposes',[]):
                    conditions='、'.join(conditional['required_capabilities']) or '该工具的原入口条件'
                    item['deployment'].append({'label':'条件型工作用途','value':conditional['purpose']+'；检查项：'+conditions})
                if current.get('work_requirement'):
                    issue=current['work_requirement']
                    item['deployment'].append({'label':'工作前置','value':requirement_labels.get(issue,issue)})
                for blocked in current.get('blocked_purposes',[]):
                    issue=blocked['requirement']
                    item['deployment'].append({'label':'当前入口缺项','value':blocked['purpose']+'：'+requirement_labels.get(issue,issue)})
        item['actions'] = list({action['name']: action for plugin in item['plugins']
                                for action in plugin.get('actions', []) if action.get('purpose') != '已声明但当前未注册'}.values())
        if ident in {'python', 'browser', 'media'}:
            backend_label = {'gateway': '独立 Gateway', 'local': '本机试用 worker'}.get(backend, '未配置')
            if ident == 'browser' and backend != 'gateway':
                backend_label = '浏览器插件自己的本机试用入口；未选择共享 Gateway'
            elif ident == 'media' and backend != 'gateway':
                backend_label = '未配置 Gateway 媒体后端；不使用本机解码'
            item['deployment'].append({'label': '已保存后端', 'value': '选择有歧义，未采用任何后端' if workspace_error else backend_label})
            item['deployment'].append({'label': '共享配置来源', 'value': workspace[0] if workspace else '未选择'})
            if workspace_error:
                item['deployment'].append({'label': '配置问题', 'value': workspace_error})
            elif ident == 'media' and backend != 'gateway':
                item['deployment'].append({'label': '缺失条件', 'value': '媒体片段需要 Gateway 媒体 worker'})
            item['deployment'].append({'label': '连接与隔离', 'value': '共享配置不开放 Python 或其他插件权限；本页不探测外部服务，实际装载和执行须结合插件状态与原记录核对'})
        if ident == 'files':
            from len_bot.media.files import file_delivery_facts
            delivery = file_delivery_facts(rt, scene_id, requester)
            item['deployment'].append({'label': '工作空间配置', 'value': '、'.join(
                f"{backend['plugin_id']} · {backend['backend']}" for backend in delivery['workspace_backends']) or '当前范围未开放'})
            item['deployment'].append({'label': '文件交付', 'value': '已开启' if rt.config.file_delivery.enabled else '未开启'})
            item['deployment'].append({'label': '平台协议', 'value': '未声明上传协议' if delivery['implementation'] is None else
                f"{delivery['implementation']} · {delivery['protocol']} · 部署核验标记：{delivery['deployment_verified']}"})
            item['deployment'].append({'label': '配置允许生成', 'value': '是' if delivery['can_generate'] else '否'})
            item['deployment'].append({'label': '具备资产登记配置', 'value': '是，实际 Gateway 快照仍待核对' if delivery['can_prepare_asset'] else '否'})
            item['deployment'].append({'label': '平台上传配置', 'value': '已声明并标记核对' if delivery['platform_configured'] else '未具备'})
            item['deployment'].append({'label': '所选目标与申请者', 'value': '未核对' if delivery['can_upload_to_target'] is None else '配置与授予通过' if delivery['can_upload_to_target'] else '尚未通过'})
            item['deployment'].append({'label': '范围说明', 'value': delivery['meaning']})
            if delivery['blocked_reason']:
                item['deployment'].append({'label': '缺失条件', 'value': delivery['blocked_reason']})
            item['file_delivery'] = delivery
        if ident == 'research':
            item['deployment'].extend([
                {'label': '系统研究', 'value': '运行中已开启' if rt.config.heartbeat_enabled else '运行中关闭'},
                {'label': '已保存研究开关', 'value': '开启' if root.runtime.heartbeat_enabled else '关闭'},
                {'label': '研究主题', 'value': '、'.join(root.runtime.heartbeat_topics) or '未填写主题'},
            ])
            subject = CapabilitySubject('system', 'scheduler', None, 'heartbeat')
            decision = authority.check(Capability.PUBLIC_RESEARCH, subject, now=now)
            item['authorization'].append({'title': '系统研究授权', **asdict(decision)})
            if scene_id:
                subject = CapabilitySubject('plugin', 'interest_share', scene_id, None)
                item['authorization'].append({'title': '本群分享授权', **asdict(authority.check(Capability.INTEREST_SHARE, subject, now=now))})
        if ident == 'account':
            plugin = plugins.get('bilibili_content') or {}
            saved = plugin.get('config')
            if saved is None:
                item['deployment'].append({'label': '账号配置', 'value': '尚未保存账号插件参数；实际装载状态见上方'})
            else:
                credentials = plugin['config_set']
                item['deployment'].extend([
                    {'label': '已保存账号', 'value': str(saved['account_uid']) if saved.get('account_uid') else '未绑定 UID'},
                    {'label': '登录态读取', 'value': '已保存为开启' if saved.get('authenticated_read_enabled') else '未开启'},
                    {'label': '凭据存在性', 'value': '；'.join(
                        label + ('已保存' if credentials.get(name) else '未保存')
                        for name, label in (('sessdata', '登录 Cookie：'), ('bili_jct', 'CSRF：'), ('buvid3', '设备 Cookie：')))},
                    {'label': '点赞每日上限', 'value': str(saved['daily_like_limit']) if 'daily_like_limit' in saved else '未记录'},
                    {'label': '收藏每日上限', 'value': str(saved['daily_favorite_limit']) if 'daily_favorite_limit' in saved else '未记录'},
                    {'label': '允许收藏夹', 'value': '、'.join(str(value) for value in saved.get('allowed_collection_ids', [])) or '未设置'},
                ])
            item['deployment'].append({'label': '核对范围', 'value':
                '这里只展示保存值；上限 0 关闭对应写入，不是今日剩余次数。凭据存在不证明登录有效；'
                '真实账号身份、配置文件权限、当前工作修订、独立授予与动作审查仍由原执行入口核对。'})
            rows = await query._rows('''SELECT action_id,account_uid,resource_id,action_type,scene_id,job_id,
                job_revision,status,attempted_at,updated_at,
                json_extract(facts_json,'$.desired_state') AS desired_state,
                json_extract(facts_json,'$.reason') AS reason
                FROM platform_actions WHERE (? IS NULL OR scene_id=?)
                ORDER BY updated_at DESC,action_id DESC LIMIT 1''', [scene_id, scene_id])
            item['recent_platform_action'] = rows[0] if rows else None
        required = {'files': (Capability.SEND_FILE,), 'account': (Capability.BILIBILI_AUTHENTICATED_READ,
                    Capability.BILIBILI_LIKE, Capability.BILIBILI_FAVORITE)}.get(ident, ())
        if required:
            if scene_id and requester:
                subject = CapabilitySubject('human', requester, scene_id, None)
                for capability in required:
                    decision = authority.check(capability, subject, now=now)
                    grant = authority.grant_for(subject, capability, now)
                    policy = authority.policy_for_grant(grant)
                    item['authorization'].append({'title': capability.value, **asdict(decision),
                        'policy': policy.model_dump() if policy else None})
            else:
                item['authorization'].append({'title': '使用资格', 'allowed': None, 'reason': '选择本群及真实发起者后才能核对授权'})
        tools = evidence_tools or tuple(declared)
        if tools:
            marks = ','.join('?' for _ in tools)
            rows = await query._rows(f'''SELECT id,event_id,scene_id,tool_name,created_at,
                json_extract(result_json,'$.status') AS status FROM tool_observations
                WHERE tool_name IN ({marks}) AND (? IS NULL OR scene_id=?)
                ORDER BY created_at DESC,id DESC LIMIT 1''', [*tools, scene_id, scene_id])
            item['recent_observation'] = rows[0] if rows else None
        worker = {'python': 'python', 'browser': 'browser', 'media': 'media'}.get(ident)
        if worker:
            rows = await query._rows('''SELECT execution_id,scene_id,job_id,job_revision,worker_type,
                state,accepted_at,ended_at,returncode FROM execution_runs
                WHERE worker_type=? AND (? IS NULL OR scene_id=?) ORDER BY accepted_at DESC LIMIT 1''',
                [worker, scene_id, scene_id])
            item['recent_execution'] = rows[0] if rows else None
        if ident in {'files', 'research', 'broadcast'}:
            condition = ("json_extract(payload,'$.file_asset_id') IS NOT NULL" if ident == 'files' else
                         "json_extract(payload,'$.plugin_origin.plugin_id')='interest_share'" if ident == 'research' else
                         "json_extract(payload,'$.plugin_origin.plugin_id') IN ('asoul_calendar','asoul_dynamics','bilibili_live_sensor')")
            rows = await query._rows(f'''SELECT id,scene_id,event_type,timestamp,
                payload AS receipt_payload,metadata AS receipt_metadata,
                json_extract(payload,'$.action_id') AS action_id,
                json_extract(payload,'$.origin_event_id') AS source_event_id,
                json_extract(payload,'$.job_id') AS job_id,
                json_extract(payload,'$.delivery_status') AS status,
                json_extract(payload,'$.delivery_unknown') AS delivery_unknown,
                json_extract(payload,'$.origin_mode') AS origin_mode,
                COALESCE(json_extract(metadata,'$.simulated'),0) AS simulated,
                json_extract(payload,'$.message_id') AS message_id,
                json_extract(payload,'$.file_id') AS file_id FROM events
                WHERE event_type IN ('MESSAGE_SENT','MESSAGE_SEND_FAILED','FILE_UPLOADED','FILE_UPLOAD_FAILED','ACTION_SHADOWED')
                AND ({condition}) AND (? IS NULL OR scene_id=?) ORDER BY rowid DESC LIMIT 1''', [scene_id, scene_id])
            item['recent_delivery'] = rows[0] if rows else None
            if item['recent_delivery']:
                receipt = item['recent_delivery']
                receipt['status'] = receipt_delivery_status(receipt['event_type'],
                    json.loads(receipt.pop('receipt_payload')), json.loads(receipt.pop('receipt_metadata')))
        items.append(item)
    return {'items': items, 'scene_id': scene_id, 'requester': requester, 'sampled_at': now,
            'chat_allowed': rt.scene_policy.chat_allowed(scene_id, requester) if scene_id else None,
            'requires_restart': rt.restart_required,
            'evidence_note': '记录分别证明工具返回、执行状态或平台回执；不自动认定整条业务已验收，历史成功也不是当前健康检查。'}
