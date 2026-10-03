import { reactive } from 'vue'
import { resetApiSession } from '../api.js'
import { restartApi } from './api/restart.js'

export const restartFlow = reactive({ open: false, busy: false, waiting: false, preview: null, error: null,
  phase: 'preview', lastConnectionError: null })

export async function openRestart() {
  restartFlow.open = true
  restartFlow.busy = true
  restartFlow.phase = 'preview'
  restartFlow.preview = null
  restartFlow.error = null
  try { restartFlow.preview = await restartApi.preview() }
  catch (error) { restartFlow.error = error }
  finally { restartFlow.busy = false }
}

export function panelChanged() {
  const value = restartFlow.preview.panel
  return value.saved === null || value.running.host !== value.saved.host || value.running.port !== value.saved.port
}

export function newPanelUrl() {
  const panel = restartFlow.preview.panel.saved
  if (panel === null || panel.port === 0) return null
  const hostname = ['0.0.0.0', '::'].includes(panel.host) ? location.hostname : panel.host
  const host = hostname.includes(':') && !hostname.startsWith('[') ? `[${hostname}]` : hostname
  return `http://${host}:${panel.port}/host/overview`
}

export async function waitForRestart() {
  restartFlow.phase = 'waiting'
  restartFlow.error = null
  restartFlow.lastConnectionError = null
  const old = restartFlow.preview.process
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    try {
      const current = await restartApi.ready()
      restartFlow.lastConnectionError = null
      if (current.pid !== old.pid || current.started_at !== old.started_at) {
        restartFlow.phase = 'ready'
        location.reload()
        return
      }
    } catch (error) { restartFlow.lastConnectionError = error }
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  restartFlow.phase = 'unavailable'
  restartFlow.error = new Error('尚未连接到新宿主。查看启动终端、systemd journal 或容器日志；配置已经保存。可再次检查连接，不会重复发起重启。')
}

export async function confirmRestart() {
  restartFlow.busy = true
  restartFlow.error = null
  try {
    restartFlow.preview = await restartApi.request()
    restartFlow.waiting = true
    // Retire requests and pages from the old process; readiness does not use its login session.
    resetApiSession()
    if (panelChanged()) restartFlow.phase = 'address-changed'
    else await waitForRestart()
  } catch (error) { restartFlow.error = error }
  finally { restartFlow.busy = false }
}
