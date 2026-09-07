// Shared API client: cookie session (same-origin).
export async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  if (res.status === 401) {
    throw new Error('unauthorized')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(item => `${item.loc?.join('.') || '参数'}: ${item.msg}`).join('；') : data.detail || `HTTP ${res.status}`)
  return data
}

export function fmtTime(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
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
    address_name: '呼唤昵称', nickname: '呼唤昵称', continuing_interaction: '连续互动', in_flight_follow_up: '运行中的追问', keyword_opportunity: '关键词观察机会', sample_opportunity: '旁听抽样机会', focus: '连续互动', focused_participant: '连续互动',
    active_work_participant: '工作参与者', work_participant: '工作参与者', work_event: '工作事件',
    runtime_event: '运行事件', keyword: '运营关键词', sample: '旁听抽样', sampling: '旁听抽样' }[reason] || reason
}
