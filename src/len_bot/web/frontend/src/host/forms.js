export const clone = value => JSON.parse(JSON.stringify(value))
export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
export const numberOrBlank = value => (value === '' || value === null ? '' : Number(value))
export const numberOrNull = value => (value === '' || value === null ? null : Number(value))
