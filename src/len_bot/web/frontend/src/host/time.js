import { host } from './store.js'

export function formatTime(seconds, timeZone, { date = true } = {}) {
  if (seconds === null || seconds === undefined) return '—'
  return new Date(seconds * 1000).toLocaleString('zh-CN', {
    timeZone: timeZone || host.state?.timezone || undefined, hour12: false,
    ...(date ? { month: 'numeric', day: 'numeric' } : {}),
    hour: '2-digit', minute: '2-digit',
  })
}

export function formatAgo(seconds) {
  const diff = Date.now() / 1000 - seconds
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}
