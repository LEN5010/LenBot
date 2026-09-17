// One name per model role. Three views used to spell these differently
// ("工作" vs "后台工作", "维护" vs "维护整理"), which read as different things.
export const roles = [
  { key:'conversation', name:'对话', description:'理解原话与图片，选择参与、文字、表情或沉默。' },
  { key:'work', name:'工作', description:'后台查询、计算和核实，形成带来源的结果。' },
  { key:'maintenance', name:'维护', description:'增量整理历史与认识，压缩工作上下文和整理方法技能。' },
]

export const roleNames = Object.fromEntries(roles.map(item => [item.key, item.name]))

export function roleName(key) {
  return roleNames[key] || key
}
