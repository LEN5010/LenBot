"""The explicit public-only heartbeat path; no other system scene is admitted."""
from __future__ import annotations

import json

PUBLIC_TOOL_OWNERS = {
    'web_search': 'web_search_tool', 'read_page': 'web_search_tool',
    'get_video_info': 'bilibili_content', 'search_bilibili': 'bilibili_content',
    'get_video_pages': 'bilibili_content', 'get_video_comments': 'bilibili_content',
    'get_video_subtitles': 'bilibili_content',
    'browser_open': 'browser_agent', 'browser_snapshot': 'browser_agent',
    'browser_interact': 'browser_agent', 'browser_capture': 'browser_agent',
    'run_python': 'workspace', 'list_workspace_files': 'workspace',
    'read_workspace_file': 'workspace', 'export_workspace_artifact': 'workspace',
}
PUBLIC_LOCAL_TOOLS = frozenset({'tool_search', 'read_tool_result', 'calculate', 'finite_check',
    'list_public_interests', 'read_web_media'})
PUBLIC_WORK_TOOLS = PUBLIC_LOCAL_TOOLS | PUBLIC_TOOL_OWNERS.keys() | {'update_work_state', 'report_progress'}
ANONYMOUS_TOOLS = frozenset(name for name, owner in PUBLIC_TOOL_OWNERS.items() if owner != 'workspace')


def has_public_context(job) -> bool:
    origin = job.get('public_research') or {}
    initiator = job.get('initiator') or {}
    return bool(job.get('scene_id') == 'system:heartbeat' and origin.get('seed_event_id')
        and origin.get('cycle_id') and origin.get('occupancy_id')
        and initiator.get('principal_type') == 'system' and initiator.get('agent_id') == 'scheduler'
        and initiator.get('purpose') == 'heartbeat' and initiator.get('cycle_id') == origin['cycle_id']
        and initiator.get('trigger_event_id') == origin['seed_event_id']
        and job.get('source_event_ids') == [origin['seed_event_id']])


async def verify_public_job(store, job) -> bool:
    if not has_public_context(job):
        return False
    origin = job['public_research']
    row = await (await store._db.execute('SELECT event_type,actor_id,payload FROM events WHERE id=? AND scene_id=?',
        (origin['seed_event_id'], job['scene_id']))).fetchone()
    if not row or row[0] != 'TASK_DUE' or row[1] != 'system:scheduler':
        return False
    payload = json.loads(row[2])
    if payload.get('task_id') != origin['cycle_id'] or payload.get('payload', {}).get('kind') != 'heartbeat':
        return False
    held = await store.get_task(origin['occupancy_id'])
    return bool(held and held.scene_id == job['scene_id'] and held.payload.get('job_id') == job['id']
                and held.payload.get('source_event_id') == origin['seed_event_id'])
