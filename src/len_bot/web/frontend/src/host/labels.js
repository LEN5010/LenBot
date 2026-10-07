import { statusText } from './status.js'

export const roleOptions = [
  { title: '主人', value: 'owner' }, { title: '管理员', value: 'admin' },
  { title: '群管理', value: 'group_manager' }, { title: '白名单', value: 'whitelist' },
  { title: '群友', value: 'member' },
]
const sections = {
  bot_id: '连接', owners: '连接', timezone: '连接', onebot: '连接', delivery: '连接',
  max_steps: '连接', turn_timeout_seconds: '连接', text_delivery: '连接', max_model_requests: '连接',
  panel: '面板账号', permissions: '权限', models: '模型', limits: 'token 与发言上限', retention: '数据保留',
  compaction: '上下文与媒体', images: '上下文与媒体', audio: '上下文与媒体', logging: '上下文与媒体',
  web_read: '网页读取', web_search: '网页搜索', network: '代理假 IP 网段', memory: '记忆', worker: '任务执行环境',
  account_browser: '账号浏览器', plugins: '插件', mcp: 'MCP', prompts: '框架提示词',
}

export const messageLabel = value => statusText('message', value)
export const turnLabel = value => statusText('turn', value)
export const runtimeLabel = value => statusText('runtime', value)
export const sectionLabel = value => sections[value] || '其他配置'
export const turnFailed = value => ['error', 'timeout', 'interrupted', 'step_limit'].includes(value)

const tools = {
  say: '发言', wait: '等待', recall_chat: '翻聊天记录', look: '看图', web_search: '搜索网页', web_read: '读网页',
  schedule: '定提醒', schedule_list: '查看提醒', schedule_cancel: '取消提醒', tool_search: '查找工具',
  memory: '记忆', delegate: '委托任务', task: '任务', send_file: '发送文件', react: '表情回应',
  persona_knowledge: '查角色资料', scene_control: '让 Bot 暂时安静', open_forward: '看合并转发', member_info: '查群成员', transcribe: '转写语音', host_manage: '管理设置',
}
const callRoles = { mind: '聊天', voice: '历史表达器', recap: '整理回想', vision: '看图', learner: '学习', worker: '后台任务',
  asr: '语音转写', learning: '学说法', jargon: '学黑话', sticker: '表情收集', reply_effects: '回复效果',
  expression_embedding: '表达向量', memory: '记忆整理', memory_summary: '记忆摘要', memory_embedding: '记忆向量' }
const notices = {
  group_recall: '撤回了一条消息', friend_recall: '撤回了一条消息', group_increase: '加入了群聊', group_decrease: '离开了群聊',
  group_ban: '禁言状态变化', group_card: '修改了群名片', notify: '平台通知', group_upload: '上传了群文件',
}
export const toolLabel = name => tools[name] || name
const toolNotes = {
  say: '在群里说话', wait: '等群友把话说完再回', recall_chat: '翻看本群以前的聊天', look: '看群里发的图片',
  web_search: '上网搜索', web_read: '打开网页读内容', schedule: '定提醒和周期安排', schedule_list: '查看定好的提醒',
  schedule_cancel: '取消提醒', tool_search: '用到不常用的工具时先把它找出来', memory: '记住、查找和修改长期记忆',
  delegate: '把耗时的活交给后台任务', task: '查看和追问后台任务', send_file: '把任务做出来的文件发到群里',
  react: '发表情回应', persona_knowledge: '查角色自带的资料', scene_control: '有人让它安静时暂停一会儿',
  open_forward: '展开合并转发看里面的内容', member_info: '查群成员的名片和头衔', transcribe: '把语音转成文字',
  host_manage: '主人在群里让它改设置',
}
export const toolNote = name => toolNotes[name] || ''
export const callRoleLabel = role => role.startsWith('plugin:') ? `插件 ${role.slice(7)}` : callRoles[role] || role
export const noticeLabel = kind => notices[kind] || kind
