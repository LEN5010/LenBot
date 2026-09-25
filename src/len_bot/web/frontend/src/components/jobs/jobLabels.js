// Range labels shared by the result and progress tabs.
export const rangeUnit = unit => ({ characters: '字符', records: '记录' }[unit] || unit)
export const spanLabel = span => `${span.start}–${span.end} ${rangeUnit(span.coordinate_unit)}`
export const rangesLabel = ranges => ranges.map(([start,end])=>`${start}–${end}`).join('、') || '没有记录采用范围'
