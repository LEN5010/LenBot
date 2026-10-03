export const continuationLabel = { active: '执行中或仍持有运行环境', retained: '保留续接', discarded: '已放弃续接', account_new_task: '账号任务，后续需新建' }
export const rootLabel = { workspace: '工作区', runtime: '运行目录', deliveries: '独立交付' }
export const fileSize = bytes => bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(2)} GB`
  : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${(bytes / 1024).toFixed(1)} KB`
