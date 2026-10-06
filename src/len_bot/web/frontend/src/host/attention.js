// Things a person needs to act on, one line and one link each. Built from the
// host state and today's overview; the home page lists them and the top bar counts them.
import { sceneName } from '../api.js'
import { runtimeLabel, turnFailed } from './labels.js'
import { formatAgo } from './time.js'

// The host can be running while QQ is not connected; say which one is wrong.
export const connectionLabel = connection => connection.status === 'running' ? 'QQ 未连接' : runtimeLabel(connection.status)

export function attentionItems(state, day) {
  const items = []
  if (state && !(state.connection.connected && state.connection.accepting)) {
    items.push({ key: 'connection', text: connectionLabel(state.connection),
      to: { name: 'host-system', query: { tab: 'connection' } }, action: '连接设置' })
  }
  const places = { 插件: name => ({ name: 'host-plugins', query: { item: name } }),
    'MCP 服务': name => ({ name: 'host-capabilities', query: { tab: 'mcp', item: name } }) }
  for (const [kind, list] of [['插件', state?.plugins || []], ['MCP 服务', state?.mcp || []]]) {
    for (const item of list) {
      if (item.status === 'failed') items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} 没有启动成功`,
        error: item.error, to: places[kind](item.name), action: '查看' })
      else if (item.latest_error) items.push({ key: `${kind}:${item.name}`, text: `${kind} ${item.name} ${formatAgo(item.latest_error.at)}报错`,
        error: item.latest_error.error, to: places[kind](item.name), action: '查看' })
    }
  }
  if (!day) return items
  for (const [scene, total] of Object.entries(day.undelivered)) {
    items.push({ key: `send:${scene}`, text: `${sceneName(scene)} 今天有 ${total} 条消息没发出去`,
      to: { name: 'host-scenes', query: { scene } }, action: '查看' })
  }
  const failures = {}
  for (const turn of day.recent_errors) {
    if (turn.started >= day.since && turnFailed(turn.status)) failures[turn.scene] = (failures[turn.scene] || 0) + 1
  }
  for (const [scene, total] of Object.entries(failures)) {
    items.push({ key: `turn:${scene}`, text: `${sceneName(scene)} 今天有 ${total} 次回复出错`,
      to: { name: 'host-logs', query: { scene } }, action: '查看' })
  }
  for (const [scene, total] of Object.entries(day.failed_tasks)) {
    items.push({ key: `task:${scene}`, text: `${sceneName(scene)} 今天有 ${total} 个任务失败`,
      to: { name: 'host-tasks', query: { scene } }, action: '查看' })
  }
  // Stuck memory ingest is one line however many groups it affects; each group is retried on the memory page.
  if (day.memory_stuck.length) {
    const jobs = day.memory_stuck
    items.push({ key: 'memory', to: { name: 'host-memory', query: { scene: jobs[0].scene, tab: 'ingest' } }, action: '去重试',
      text: jobs.length === 1
        ? `${sceneName(jobs[0].scene)} 的记忆整理${jobs[0].status === 'failed' ? '失败' : '被中断'}，停在这里等你重试`
        : `${jobs.length} 个群的记忆整理停住了，需要逐个重试：${jobs.map(job => sceneName(job.scene)).join('、')}`,
      error: jobs.map(job => `${sceneName(job.scene)}（${job.status === 'failed' ? '失败' : '中断'}）：${job.error}`).join('\n\n') })
  }
  for (const [scene, reviews] of Object.entries(day.pending_reviews)) {
    const parts = [['expressions', '条表达'], ['stickers', '张表情']]
      .filter(([key]) => reviews[key]).map(([key, unit]) => `${reviews[key]} ${unit}`)
    items.push({ key: `review:${scene}`, text: `${sceneName(scene)} 新学到 ${parts.join('、')}，等你审核`,
      to: { name: 'host-scenes', query: { scene, tab: 'learning' } }, action: '去审核' })
  }
  return items
}
