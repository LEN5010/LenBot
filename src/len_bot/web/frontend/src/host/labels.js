// Everyday wording for host states. Unknown values fall back to the raw value
// so a new backend state shows up instead of disappearing.
const messages = {
  received: '收到', sent: '已发送', simulated: '模拟发送（未发到 QQ）',
  failed: '发送失败', unconfirmed: '不确定是否发出',
}
const turns = {
  queued: '排队中', running: '进行中', settling: '收尾中', settled: '已完成', error: '失败',
  timeout: '超时', cancelled: '已取消', interrupted: '已中断', step_limit: '步数用完',
}
const runtime = {
  created: '准备中', starting: '启动中', waiting_connection: '等待 QQ 连接',
  running: '运行中', stopping: '正在停止', stopped: '已停止',
}
export const roleOptions = [
  { title: '主人', value: 'owner' }, { title: '管理员', value: 'admin' },
  { title: '群管理', value: 'group_manager' }, { title: '白名单', value: 'whitelist' },
  { title: '群友', value: 'member' },
]
// Root config fields as named in GET /api/host/pending-restart.
const sections = {
  bot_qq: '连接', owner_qq: '连接', timezone: '连接', onebot: '连接', delivery: '连接',
  max_steps: '连接', turn_timeout_seconds: '连接', text_delivery: '连接', max_model_requests: '连接',
  panel: '面板账号', permissions: '权限', models: '模型', limits: '花费上限', retention: '数据保留',
  compaction: '上下文与媒体', images: '上下文与媒体', audio: '上下文与媒体', logging: '上下文与媒体',
  web_read: '网页读取', web_search: '网页搜索', memory: '记忆', worker: '任务执行环境',
  account_browser: '账号浏览器', plugins: '插件', mcp: 'MCP',
}

export const messageLabel = value => messages[value] || value
export const turnLabel = value => turns[value] || value
export const runtimeLabel = value => runtime[value] || value
export const sectionLabel = value => sections[value] || '其他配置'
export const turnFailed = value => ['error', 'timeout', 'interrupted', 'step_limit'].includes(value)
