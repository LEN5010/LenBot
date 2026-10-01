// The ten user-facing areas, with actual pages grouped beneath them.
export const hostAreas = [
  { id: 'home', title: '首页', name: 'host-overview', pages: [['今日概览', 'host-overview']] },
  { id: 'trial', title: '对话测试', name: 'host-trials', pages: [['隔离试聊', 'host-trials']] },
  { id: 'scenes', title: '群聊', name: 'host', pages: [['消息', 'host'], ['设置', 'host-settings'], ['大脑', 'host-history'], ['学习', 'host-scene-learning']] },
  { id: 'persona', title: '角色', name: 'host-persona', pages: [['角色包', 'host-persona']] },
  { id: 'memory', title: '记忆', name: 'host-memory', pages: [['认识与记忆', 'host-memory'], ['学习成果', 'host-learning']] },
  { id: 'tasks', title: '任务', name: 'host-tasks', pages: [['独立任务', 'host-tasks'], ['定时安排', 'host-schedules']] },
  { id: 'capabilities', title: '能力', name: 'host-capabilities', pages: [['工具、技能与服务', 'host-capabilities']] },
  { id: 'models', title: '模型', name: 'host-models', pages: [['模型与价格', 'host-models']] },
  { id: 'logs', title: '日志', name: 'host-logs', pages: [['消息与轮次', 'host-logs'], ['系统', 'host-system-logs']] },
  { id: 'settings', title: '设置', name: 'host-system', pages: [['设置', 'host-system']] },
]
export const hostPageNames = hostAreas.flatMap(area => area.pages.map(([, name]) => name))
export const hostPaths = ['/host', '/host/overview', '/host/chat-test', '/host/scenes/learning', '/host/logs', '/host/logs/system',
  '/host/capabilities', '/host/models', '/host/settings', '/host/persona', '/host/system', '/host/history',
  '/host/memory', '/host/learning', '/host/tasks', '/host/schedules']
export function hostTarget(name, route) {
  return { name, query: typeof route.query.scene === 'string' ? { scene: route.query.scene } : {} }
}
