// The ten user-facing areas. `pages` are the route names that belong to an area.
export const hostAreas = [
  { id: 'home', title: '首页', name: 'host-overview', pages: ['host-overview'] },
  { id: 'trial', title: '对话测试', name: 'host-trials', pages: ['host-trials'] },
  { id: 'scenes', title: '群聊', name: 'host-scenes', pages: ['host-scenes'] },
  { id: 'persona', title: '角色', name: 'host-persona', pages: ['host-persona'] },
  { id: 'memory', title: '记忆', name: 'host-memory', pages: ['host-memory'] },
  { id: 'tasks', title: '任务', name: 'host-tasks', pages: ['host-tasks'] },
  { id: 'capabilities', title: '能力', name: 'host-capabilities', pages: ['host-capabilities'] },
  { id: 'models', title: '模型', name: 'host-models', pages: ['host-models'] },
  { id: 'logs', title: '日志', name: 'host-logs', pages: ['host-logs'] },
  { id: 'settings', title: '设置', name: 'host-system', pages: ['host-system'] },
]
export const hostPageNames = hostAreas.flatMap(area => area.pages)
export const hostPaths = ['/host/overview', '/host/chat-test', '/host/scenes', '/host/logs',
  '/host/capabilities', '/host/models', '/host/persona', '/host/system', '/host/memory',
  '/host/tasks']
export function hostTarget(name, route) {
  return { name, query: typeof route.query.scene === 'string' ? { scene: route.query.scene } : {} }
}
