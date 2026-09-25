// Shared by the access and member tabs; the message names the field.
export function positiveInteger(value, label) {
  const text = String(value ?? '').trim()
  const number = Number(text)
  if (!/^[1-9]\d*$/.test(text) || !Number.isSafeInteger(number)) throw new Error(`${label}须填写有效正整数`)
  return number
}
