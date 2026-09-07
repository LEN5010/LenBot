const purposes={conversation:'对话',work:'后台工作',history_maintenance:'历史维护',work_compression:'工作压缩',skill_maintenance:'技能整理',capability_probe:'能力检查'}
const events={GROUP_MESSAGE_RECEIVED:'群聊原话',PRIVATE_MESSAGE_RECEIVED:'私聊原话',MESSAGE_SENT:'发送回执',MESSAGE_SEND_FAILED:'发送未成功',ACTION_SHADOWED:'Shadow 候选',CONVERSATION_COMMITTED:'对话已提交',TASK_DUE:'提醒到期',TASK_REVIEW:'任务待核对',AGENT_JOB_CONTROL:'工作变更',AGENT_JOB_PROGRESS:'工作进展',AGENT_JOB_FINISHED:'工作执行结束',AGENT_JOB_CHECKPOINT:'工作检查点',TOOL_OBSERVATION_RECORDED:'工具资料',HISTORY_COMPACTION_RECORDED:'历史摘要',REFLECTION_RECORDED:'维护认识',MEDIA_UPDATED:'媒体更新',OPERATOR_ACTION:'运营操作',LIVE_STARTED:'直播开始',LIVE_ENDED:'直播结束',TOOL_COMPLETED:'感知工具返回',USER_JOINED:'成员加入'}
export const purposeOptions=Object.entries(purposes).map(([value,title])=>({value,title}))
export function purposeLabel(value){return purposes[value] || value}
export function eventLabel(value){return events[value] || value}
export function traceLabel(value){return {conversation:'对话',conversation_error:'对话失败',agent_job:'信息工作',agent_job_error:'工作失败',history_maintenance:'历史维护',history_maintenance_error:'历史维护失败',work_compression:'工作压缩',skill_maintenance:'技能整理'}[value] || value}
