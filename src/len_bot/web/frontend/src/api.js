// Shared API client: cookie session (same-origin), no retries or optimistic writes.
import { ref } from 'vue'
const displayTimezone = ref(null)
export function setDisplayTimezone(value) { displayTimezone.value = value }
let onUnauthorized = () => {}
let sessionGeneration = 0
export function setUnauthorizedHandler(handler) { onUnauthorized = handler }
export function resetApiSession() { ++sessionGeneration }

export async function api(path, options = {}) {
  const session = sessionGeneration
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  if (res.status === 401 && path !== '/api/auth/login' && session === sessionGeneration) onUnauthorized()
  let data
  try { data = await res.json() }
  catch {
    const error = new Error(`HTTP ${res.status}：接口未返回可读取的 JSON，本次请求结果需核对；没有自动重试。`)
    error.status = res.status
    throw error
  }
  if (session !== sessionGeneration) {
    const error = new Error('请求所属登录状态已变化，未采用旧响应；已提交操作是否完成须回到原对象核对。')
    error.status = res.status
    throw error
  }
  if (!res.ok) {
    const error = new Error(Array.isArray(data.detail) ? data.detail.map(item => `${item.loc?.join('.') || '参数'}: ${item.msg}`).join('；') : data.detail?.message || data.detail || `HTTP ${res.status}`)
    error.status = res.status
    error.details = data.detail
    throw error
  }
  return data
}

export function queryString(values) {
  return new URLSearchParams(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined)).toString()
}

export function sceneName(id) {
  if (id === 'global-safe') return '公共素材'
  if (!id) return '全部场景'
  const [kind, ...parts] = id.split(':')
  return `${kind === 'group' ? '群聊' : kind === 'private' ? '私聊' : kind} ${parts.join(':')}`
}

export function fmtTime(ts) {
  if (ts === null || ts === undefined) return '—'
  const date = new Date(ts * 1000)
  if (displayTimezone.value === null) return date.toISOString()
  return date.toLocaleString('zh-CN', { hour12: false, timeZone: displayTimezone.value, timeZoneName: 'short' })
}

export function fmtAgo(ts) {
  if (!ts) return '—'
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return `${Math.floor(diff)}s 前`
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`
  return `${Math.floor(diff / 86400)}d 前`
}


export function attentionReason(reason) {
  return { private: '私聊', private_message: '私聊', mention: '真实 @', reply: '回复 Bot', reply_to_bot: '回复 Bot',
    address_name: '出现名称线索', nickname: '呼唤昵称', continuing_interaction: '连续互动', in_flight_follow_up: '运行中的追问', keyword_opportunity: '关键词观察机会', sample_opportunity: '周期待观察', active_observation: '短时观察', in_flight_observation: '运行中跟读', focus: '连续互动', focused_participant: '连续互动',
    active_work_participant: '工作参与者', work_participant: '工作参与者', work_event: '工作事件',
    runtime_event: '运行事件', keyword: '运营关键词', sample: '旁听观察', sampling: '旁听观察' }[reason] || reason
}
