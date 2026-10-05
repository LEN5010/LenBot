// Every status the panel shows, as [text, tone]. Tone picks the color:
// success, warning, error, info (in progress) or neutral.
const tables = {
  runtime: {
    created: ['准备中', 'info'], starting: ['启动中', 'info'], waiting_connection: ['等待 平台账号 连接', 'warning'],
    running: ['运行中', 'success'], connection_failed: ['平台账号 连接失败', 'error'], failed: ['业务运行失败', 'error'],
    stopping: ['正在停止', 'warning'], stopped: ['已停止', 'neutral'],
  },
  turn: {
    queued: ['排队中', 'neutral'], running: ['进行中', 'info'], settling: ['收尾中', 'info'], settled: ['已完成', 'success'],
    error: ['失败', 'error'], timeout: ['超时', 'error'], cancelled: ['已取消', 'neutral'], interrupted: ['已中断', 'error'],
    step_limit: ['步数用完', 'error'],
  },
  message: {
    received: ['收到', 'neutral'], sent: ['已发送', 'success'], simulated: ['模拟发送（未发到 平台账号）', 'neutral'],
    failed: ['发送失败', 'error'], unconfirmed: ['不确定是否发出', 'warning'],
  },
  plugin: {
    running: ['运行中', 'success'], loaded: ['已加载', 'success'], failed: ['没有启动成功', 'error'], stopped: ['已停止', 'neutral'],
    pending: ['重启后加载', 'warning'], unloaded: ['未加载', 'neutral'],
  },
  mcp: {
    disabled: ['未启用', 'neutral'], connecting: ['连接中', 'info'], running: ['已连接', 'success'],
    failed: ['连接失败', 'error'], stopped: ['已断开', 'neutral'],
  },
  task: {
    queued: ['排队中', 'neutral'], running: ['进行中', 'info'], waiting_input: ['等你回答', 'warning'],
    done: ['已完成', 'success'], failed: ['失败', 'error'], cancelled: ['已取消', 'neutral'],
  },
  schedule: {
    pending: ['等待中', 'info'], blocked: ['卡住了', 'warning'], delivered: ['已提醒', 'success'], cancelled: ['已取消', 'neutral'],
  },
  ingest: {
    queued: ['排队中', 'neutral'], running: ['整理中', 'info'], complete: ['完成', 'success'], failed: ['失败', 'error'],
    interrupted: ['中断了', 'warning'],
  },
  summary: {
    running: ['进行中', 'info'], complete: ['完成', 'success'], failed: ['失败', 'error'], interrupted: ['中断', 'warning'],
  },
  audio: {
    idle: ['未转写', 'neutral'], queued: ['排队中', 'neutral'], running: ['转写中', 'info'], complete: ['已转写', 'success'],
    failed: ['转写失败', 'error'], interrupted: ['已中断', 'warning'],
  },
  upload: { uploaded: ['已发到 平台账号', 'success'], failed: ['发送失败', 'error'], unconfirmed: ['不确定是否发出', 'warning'] },
  live: { connected: ['实时更新中', 'success'], connecting: ['正在连接…', 'info'], closed: ['实时更新已断开', 'warning'] },
}

// Unknown values show the raw value so a new backend state stays visible.
export function statusOf(kind, value) {
  const [text, tone] = tables[kind]?.[value] || [value, 'neutral']
  return { text, tone }
}
export const statusText = (kind, value) => statusOf(kind, value).text
export const toneColor = tone => tone === 'neutral' ? undefined : tone
