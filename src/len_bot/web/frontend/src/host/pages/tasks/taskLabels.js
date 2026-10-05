const events = {
  tool_execution_start: '开始操作', tool_execution_end: '操作结果', message_start: '开始写', message_end: '写完一段',
  agent_start: '开始执行', agent_end: '这次执行结束', extension_ui_request: '需要补充或确认', finished: '任务结束',
  progress: '进度', answer_timeout: '等回答超时', input: '追加要求', question: '提问', answer: '收到回答',
  browser_started: '打开了专用浏览器', browser_stopped: '关闭了专用浏览器', browser_released: '清理了浏览器会话',
  browser_file: '浏览器文件已保存', browser_upload: '文件已附加到网页控件',
  workspace_discard: '放弃任务环境', workspace_discard_result: '清理任务环境', material_inputs: '放入了资料',
}
export const eventLabel = value => events[value] || value
export const finished = status => ['done', 'failed', 'cancelled'].includes(status)
