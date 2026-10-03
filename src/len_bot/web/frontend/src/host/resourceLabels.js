export const resourceScopes = { workspace: '工作区', inputs: '输入快照', deliveries: '登记交付', runtime: '运行目录', shared: '共享资料' }

export function resourceLabel(reference) {
  const task = reference.task_id === null ? '' : `任务 #${reference.task_id} · `
  const file = reference.scope === 'deliveries' ? `#${reference.file_id}` : reference.path
  return `${task}${resourceScopes[reference.scope]} / ${file}`
}
