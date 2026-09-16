"""Current work facts shared by the local and Gateway execution drivers."""
from len_bot.runtime.public_research import verify_public_job


async def require_execution_job(store, call, plugin_id):
    if call.role != 'work' or not call.job_id or call.job_revision is None:
        raise ValueError('执行需要已有工作及原调用的明确修订')
    if call.plugin is None:
        raise ValueError('执行调用缺少所属插件，不能核对当前使用资格')
    await call.plugin._host.validate_call(call)
    job = await store.get_job(call.job_id, call.scene_id)
    if not job or job['requester_qq_uid'] != call.requester_qq_uid:
        raise ValueError('执行调用发起人与工作归属不一致')
    if job['revision'] != call.job_revision or job['status'] != 'processing':
        raise ValueError('原调用的工作修订已改变或不在执行中，不能开始新操作')
    origin = job.get('plugin_origin') or {}
    if origin and origin.get('plugin_id') != plugin_id:
        raise ValueError('执行不能使用其他插件工作的身份')
    deadline = (job.get('budget') or {}).get('deadline_at')
    if deadline is not None and store.clock() >= deadline:
        raise ValueError('原工作执行期限已到，不能开始新操作')
    if not call.requester_qq_uid and not await verify_public_job(store, job):
        raise ValueError('系统执行没有已验证的公共研究归属')
    return job
