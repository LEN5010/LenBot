// 仅携带面板的定位与筛选字段；返回路径不嵌套，也不复制凭据参数。
const locationKeys = new Set([
  'tab', 'scene', 'event', 'id', 'version', 'query', 'q', 'permission', 'type',
  'page', 'before', 'snapshot', 'cursors', 'delivery', 'subject', 'status', 'kind',
  'execution', 'list_scene', 'object_scene', 'resource', 'result', 'episode',
  'purpose', 'actor', 'event_type', 'level', 'since', 'until', 'requester', 'pane', 'enabled',
  'result_start', 'result_end', 'result_unit', 'file', 'interest',
])

export function internalPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || /[\\\u0000-\u0020]/.test(value)) return ''
  const path = value.split(/[?#]/, 1)[0]
  let decoded
  try { decoded = decodeURIComponent(path) } catch { return '' }
  if (decoded.startsWith('//') || /[\\\u0000-\u0020]/.test(decoded) || /^\/login(?:\/|$)/.test(decoded)) return ''
  const url = new URL(value, 'https://panel.invalid')
  if (url.origin !== 'https://panel.invalid' || /^\/login(?:\/|$)/.test(url.pathname)) return ''
  return value
}

function locationQuery(query) {
  const params = new URLSearchParams()
  for (const key of Object.keys(query).sort()) {
    const value = query[key]
    if (locationKeys.has(key) && (typeof value === 'string' || typeof value === 'number')) params.set(key, String(value))
  }
  return params.toString()
}

export function sourcePath(route) {
  if (!internalPath(route.path)) return ''
  const query = locationQuery(route.query)
  return route.path + (query ? `?${query}` : '')
}

export function returnTarget(value) {
  if (!internalPath(value)) return ''
  const url = new URL(value, 'https://panel.invalid')
  if (url.origin !== 'https://panel.invalid') return ''
  if (!/^\/(?:overview|scenes|groups|jobs|tasks|memories|skills|media|models|agent|plugins|settings|activity)(?:\/|$)/.test(url.pathname)) return ''
  return sourcePath({ path: url.pathname, query: Object.fromEntries(url.searchParams) })
}

export function withReturn(route, target) {
  const sameScene = route.name === 'scene' && target.name === 'scene' && route.params.sceneId === target.params?.sceneId
  const origin = returnTarget(route.query.return_to) || (sameScene ? '' : sourcePath(route))
  return { ...target, query: { ...target.query, return_to: origin || undefined } }
}
