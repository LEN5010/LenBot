import { clone } from './forms.js'

export function initialField(field, value = field.default) {
  if (field.type === 'secret') return ''
  if (value == null) {
    if (field.type === 'boolean') return false
    if (field.type === 'scene') return null
    if (field.type === 'scene_list') return []
    if (field.type === 'object_list') return field.fields.length ? [] : '[]'
    return ''
  }
  if (field.type === 'object_list') {
    return field.fields.length
      ? value.map(row => Object.fromEntries(field.fields.map(child => [child.key, initialField(child, row[child.key])])))
      : JSON.stringify(value, null, 2)
  }
  return field.type === 'string_list' ? value.join('\n') : clone(value)
}

export function configValue(field, value) {
  if (field.type === 'object_list') {
    if (field.fields.length) return value.map(row => Object.fromEntries(field.fields
      .map(child => [child.key, configValue(child, row[child.key])])
      .filter(([, item]) => item !== undefined)))
    let items
    try { items = JSON.parse(value) } catch (error) { throw new Error(`${field.label || field.key} 不是合法的 JSON：${error.message}`) }
    if (!Array.isArray(items) || items.some(item => item === null || typeof item !== 'object' || Array.isArray(item))) {
      throw new Error(`${field.label || field.key} 必须是 JSON 对象列表`)
    }
    return items
  }
  if (field.type === 'string_list') return value.split('\n').map(item => item.trim()).filter(Boolean)
  if (field.type === 'integer' || field.type === 'number') return value === '' || value === null ? undefined : Number(value)
  if (field.type === 'scene') return value ?? undefined
  return value
}
