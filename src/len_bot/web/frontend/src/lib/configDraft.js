// Only used after an operator explicitly keeps a conflicted configuration
// draft. Unedited fields take the newly read value; arrays remain whole edits,
// matching the server's config_edit contract. This never saves or retries.
const absent = Symbol('absent configuration field')
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const copy = value => value === absent ? absent : JSON.parse(JSON.stringify(value))

function same(left, right) {
  if (left === right) return true
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => same(value, right[index]))
  if (!object(left) || !object(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && same(left[key], right[key]))
}

export const hasConfigDraftChanges = (original, draft) => !same(original, draft)

export function rebaseConfigDraft(original, draft, current) {
  if (same(original, draft)) return copy(current)
  if (!object(original) || !object(draft) || !object(current)) return copy(draft)
  const keys = new Set([...Object.keys(current), ...Object.keys(original), ...Object.keys(draft)])
  const rows = []
  for (const key of keys) {
    const value = rebaseConfigDraft(
      Object.hasOwn(original, key) ? original[key] : absent,
      Object.hasOwn(draft, key) ? draft[key] : absent,
      Object.hasOwn(current, key) ? current[key] : absent,
    )
    if (value !== absent) rows.push([key, value])
  }
  return Object.fromEntries(rows)
}
