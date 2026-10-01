export function internalPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || /[\\\u0000-\u0020]/.test(value)) return ''
  const path = value.split(/[?#]/, 1)[0]
  let decoded
  try {
    decoded = decodeURIComponent(path)
  } catch {
    return ''
  }
  if (decoded.startsWith('//') || /[\\\u0000-\u0020]/.test(decoded) || /^\/login(?:\/|$)/.test(decoded)) return ''
  const url = new URL(value, 'https://panel.invalid')
  if (url.origin !== 'https://panel.invalid' || /^\/login(?:\/|$)/.test(url.pathname)) return ''
  return value
}
