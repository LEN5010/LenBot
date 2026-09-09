const purposes={conversation:'对话',plugin_agent:'插件 Agent',announcement:'旧开播邀请',work:'后台工作',history_maintenance:'历史维护',work_compression:'工作压缩',skill_maintenance:'技能整理',capability_probe:'能力检查'}
const events={GROUP_MESSAGE_RECEIVED:'群聊原话',PRIVATE_MESSAGE_RECEIVED:'私聊原话',MESSAGE_SENT:'发送回执',MESSAGE_SEND_FAILED:'发送未成功',ACTION_SHADOWED:'Shadow 候选',CONVERSATION_COMMITTED:'对话已提交',TASK_DUE:'提醒到期',TASK_REVIEW:'任务待核对',AGENT_JOB_CONTROL:'工作变更',AGENT_JOB_PROGRESS:'工作进展',AGENT_JOB_FINISHED:'工作执行结束',AGENT_JOB_CHECKPOINT:'工作检查点',TOOL_OBSERVATION_RECORDED:'工具资料',HISTORY_COMPACTION_RECORDED:'历史摘要',REFLECTION_RECORDED:'维护认识',MEDIA_UPDATED:'媒体更新',OPERATOR_ACTION:'运营操作',LIVE_STARTED:'直播开始',LIVE_ENDED:'直播结束',TOOL_COMPLETED:'感知工具返回',USER_JOINED:'成员加入'}
export const purposeOptions=Object.entries(purposes).map(([value,title])=>({value,title}))
export function purposeLabel(value){return purposes[value] || value}
export function eventLabel(value){return value==='PLUGIN_EVENT'?'插件事件':events[value] || value}
const traces={conversation:'对话',conversation_error:'对话失败',plugin_run:'插件运行',plugin_work_delivery:'插件工作交付',plugin_hook:'插件回执钩子',plugin_lifecycle:'插件起停',calendar_command:'旧日程命令',live_announcement:'旧开播邀请',agent_job:'信息工作',agent_job_error:'工作失败',history_maintenance:'历史维护',history_maintenance_error:'历史维护失败',work_compression:'工作压缩',skill_maintenance:'技能整理'}
export const traceOptions=Object.entries(traces).map(([value,title])=>({value,title}))
export function traceLabel(value){return traces[value] || value}
export function traceRuns(payload){
  const collect=record=>[record,...['runs','agents'].flatMap(key=>(record[key] || []).flatMap(collect))]
  return collect(payload.conversation || payload.cognition || payload)
}
export function publicationActionLabel(value){return {not_enqueued:'尚未入队',enqueued:'已入队，送达见回执',enqueue_unknown:'入队结果未确认'}[value] || '入队结果未记录'}
export function interactionReason(value){return {plugin_consumed:'插件已认领并消费，执行结果见关联记录',prepared_work_delivery:'已生成的插件成品沿工作关系交付，回执另行记录',plugin_work_unavailable:'插件工作当前不可执行，原记录保留',scene_entry_closed:'当前入口未开放',group_disabled:'本群停用，只保存原话',chat_closed_for_requester:'普通聊天关闭，此 QQ 账号不在回复白名单',calendar_command:'旧日程命令记录',calendar_comment:'旧日程引用评论',calendar_response:'旧日程响应记录',announcement:'旧开播公告记录',chat_eligible:'具备普通聊天资格，仍由注意力与对话决定是否参与'}[value] || value}
