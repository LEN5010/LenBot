const states = {
  platform_action: {
    confirmed:['已有确认记录','success'],
    not_sent:['未发出写请求','default'],
    rejected:['动作被拒绝','error'],
    unknown:['动作结果未知','warning']
  },
  schedule_slot: {
    pending:['待触发','info'],
    claimed:['已认领','info'],
    processing:['本槽处理中','info'],
    completed:['本槽已结束','default'],
    cancelled:['本槽已取消','default'],
    failed:['本槽失败','error'],
    delivery_unknown:['结果未知','warning'],
    review_required:['待核对','warning'],
    shadow_observed:['隔离观察','default'],
    simulated:['模拟观察','default']
  },
  external_execution: {
    accepted:['已登记执行','info'],
    starting:['正在启动','info'],
    running:['进程运行中','info'],
    exited:['进程已退出','default'],
    failed:['执行失败','error'],
    cancel_requested:['已请求停止','warning'],
    termination_confirmed:['停止已确认','default'],
    termination_unconfirmed:['停止未确认','warning']
  },
  job_execution: {
    completed:['执行完成','success'],
    partial:['部分完成','warning'],
    failed:['执行失败','error'],
    interrupted:['已中断','warning'],
    cancelled:['已停止','default'],
    pending:['未开始','default'],
    running:['执行中','info'],
    processing:['执行中','info'],
    unknown:['执行结局未记录','default']
  },
  job_delivery: {
    not_required:['无需群交付','default'],
    pending:['待执行','default'],
    claimed:['已认领','info'],
    processing:['处理中','info'],
    review_required:['待核对','warning'],
    result_ready:['待回应','info'],
    awaiting_delivery:['待回执','warning'],
    completed:['已交付','success'],
    cancelled:['已停止','default'],
    failed:['未送达','error'],
    delivery_unknown:['送达未知','warning'],
    shadow_observed:['Shadow','default']
  },
  task: {
    pending:['待触发','info'],
    claimed:['已认领','info'],
    processing:['处理中','info'],
    result_ready:['待处理结果','info'],
    awaiting_delivery:['待回执','warning'],
    completed:['任务完成','success'],
    cancelled:['已取消','default'],
    failed:['失败','error'],
    delivery_unknown:['送达未知','warning'],
    review_required:['待核对','warning'],
    shadow_observed:['Shadow','default'],
    simulated:['模拟观察','default']
  },
  waiting: {
    review_required:['待核对','warning'],
    active:['等待中','info'],
    resolved:['已结束','success'],
    expired:['已过期','default'],
    cancelled:['已取消','default']
  },
  memory: {
    active:['有效','success'],
    refuted:['已撤销','error'],
    superseded:['已替代','default'],
    expired:['已过期','default']
  },
  basis: { reported:['原话报告','info'], inferred:['有据推断','warning'] },
  summary: {
    pending:['待维护','default'],
    processing:['维护中','info'],
    completed:['已覆盖','success'],
    failed:['失败','error'],
    interrupted:['已中断','warning']
  },
  attention: {
    stored_only:['仅存储','default'],
    pending:['待处理','info'],
    read:['本轮已读','info'],
    handled:['本条已处理','success'],
    unhandled:['未列为处理来源','default'],
    silence:['模型沉默','default'],
    expression:['提出表达','info'],
    sampled:['观察机会','info']
  },
  delivery: {
    sent:['已送达','success'],
    not_sent:['未送达','error'],
    rejected:['被拒绝','error'],
    unknown:['送达未知','warning'],
    shadow:['Shadow','default'],
    simulated:['模拟回执','default'],
    pending:['待回执','warning']
  },
  file_upload: {
    prepared:['资产已登记','default'],
    submitted:['已提交，尚无尝试','info'],
    uploaded:['已有真实上传回执','success'],
    failed:['未上传或被拒绝','error'],
    not_sent:['未上传','error'],
    rejected:['上传被拒绝','error'],
    unknown:['上传结局未知','warning'],
    shadow:['Shadow 观察','default'],
    simulated:['模拟回执','default']
  },
  call: {
    completed:['请求完成','success'],
    failed:['请求失败','error'],
    cancelled:['已取消','warning'],
    unconfirmed:['未确认','warning']
  },
  skill_candidate: {
    pending:['候选待整理','default'],
    processing:['整理中','info'],
    saved:['已保存','success'],
    skipped:['已跳过','default'],
    obsolete:['来源版本已过期','warning'],
    completed:['已保存','success'],
    failed:['整理失败','error'],
    interrupted:['已中断','warning'],
    rejected:['未采纳','default']
  },
  observation: {
    ok:['取得资料','success'],
    partial:['部分资料','warning'],
    no_results:['没有结果','default'],
    error:['工具返回错误','error'],
    unsupported:['当前不可用','warning']
  },
  provider: {
    ready:['配置就绪','success'],
    enabled:['已启用','info'],
    disabled:['已停用','default'],
    missing:['未配置','warning'],
    unavailable:['配置未就绪','warning']
  },
  plugin: {
    loaded:['已加载','default'],
    enabled:['运行中','success'],
    disabled:['已停用','default'],
    error:['运行异常','error'],
    unconfigured:['未配置','warning'],
    not_loaded:['尚未装载','default']
  },
  // What a group is allowed to do, as the group list shows it at a glance.
  scene_chat: {
    on:['聊天','success'],
    listen:['跟读','info'],
    off:['仅播报','default'],
    unconfigured:['未配置','warning'],
    disabled:['已停用','default']
  },
  scene_work: {
    on:['工作插件已开启','info'],
    off:['工作插件未开启','default'],
    unconfigured:['未配置','warning']
  },
}
export function statusInfo(domain, status) {
  if (status === null || status === undefined || status === '') return { label:'未记录', color:'default', known:true }
  const item = states[domain]?.[status]
  return item ? { label:item[0], color:item[1], known:true } : { label:`未识别 · ${status}`, color:'warning', known:false }
}
