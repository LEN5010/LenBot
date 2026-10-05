// Shared API client: cookie session (same-origin), no retries or optimistic writes.
import { reactive } from 'vue'

let onUnauthorized = () => {}
let sessionGeneration = 0
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler
}
export function resetApiSession() {
  ++sessionGeneration
}

export async function api(path, options = {}, responseFormat = 'json') {
  const session = sessionGeneration
  const headers = { ...(options.headers || {}) }
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  if (res.status === 401 && path !== '/api/auth/login' && session === sessionGeneration) onUnauthorized()
  let data
  try {
    data = responseFormat === 'blob' && res.ok ? await res.blob() : await res.json()
  }
  catch (problem) {
    const format = responseFormat === 'blob' && res.ok ? '二进制文件' : 'JSON'
    const error = new Error(`HTTP ${res.status}：返回的内容不是 ${format}：${problem.message}`)
    error.status = res.status
    throw error
  }
  if (session !== sessionGeneration) {
    const error = new Error('登录状态变了，请刷新页面。')
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

// Group names and private nicknames read from 平台账号; until one arrives the
// scene shows by its number.
export const sceneTitles = reactive({})

export function sceneNumber(id) {
  const [kind, ...parts] = id.split(':')
  return `${kind === 'group' ? '群' : kind === 'private' ? '私聊' : kind} ${parts.join(':')}`
}

export function sceneName(id) {
  if (id === 'global-safe') return '公共素材'
  if (!id) return '全部场景'
  return sceneTitles[id] || sceneNumber(id)
}
