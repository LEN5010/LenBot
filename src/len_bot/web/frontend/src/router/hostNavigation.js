export const hostGroups = [
  { title: '运行', areas: [
    { id: 'home', title: '首页', name: 'host-overview', pages: ['host-overview'] },
    { id: 'trial', title: '对话测试', name: 'host-trials', pages: ['host-trials'] },
    { id: 'logs', title: '日志', name: 'host-logs', pages: ['host-logs'] },
  ] },
  { title: '群聊', areas: [
    { id: 'scenes', title: '群聊', name: 'host-scenes', pages: ['host-scenes'] },
    { id: 'persona', title: '角色', name: 'host-persona', pages: ['host-persona'] },
    { id: 'memory', title: '记忆', name: 'host-memory', pages: ['host-memory'] },
    { id: 'tasks', title: '任务', name: 'host-tasks', pages: ['host-tasks'] },
    { id: 'resources', title: '资源', name: 'host-resources', pages: ['host-resources'] },
  ] },
  { title: '扩展', areas: [
    { id: 'plugins', title: '插件', name: 'host-plugins', pages: ['host-plugins'] },
    { id: 'capabilities', title: '能力', name: 'host-capabilities', pages: ['host-capabilities'] },
  ] },
  { title: '配置', areas: [
    { id: 'models', title: '模型', name: 'host-models', pages: ['host-models'] },
    { id: 'settings', title: '设置', name: 'host-system', pages: ['host-system'] },
  ] },
]
export const hostAreas = hostGroups.flatMap(group => group.areas)
export const hostPageNames = hostAreas.flatMap(area => area.pages)
export const hostPaths = ['/host/overview', '/host/chat-test', '/host/scenes', '/host/logs',
  '/host/capabilities', '/host/plugins', '/host/models', '/host/persona', '/host/system', '/host/memory',
  '/host/tasks', '/host/resources']
export function hostTarget(name, scene) {
  return { name, query: scene ? { scene } : {} }
}
