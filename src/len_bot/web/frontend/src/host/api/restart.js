import { api } from '../../api.js'

/**
 * @typedef {{pid:number, started_at:number}} HostProcess
 * @typedef {{host:string, port:number}} PanelAddress
 * @typedef {{process:HostProcess, restartable:boolean, intent:string,
 * pending:{sections:string[],scenes:string[],personas:{name:string,path:string}[],plugins:{name:string,candidate:{version:string},error:string|null}[]},
 * panel:{running:PanelAddress,saved:PanelAddress|null}, chats:string[],
 * tasks:{id:number,scene:string,goal:string,status:string,account_browser:boolean}[],
 * queued_tasks:number, browsers:{id:number,scene:string,session:string|null}[],
 * trials:{id:string,scene:string}[]}} RestartPreview
 */
export const restartApi = {
  /** @returns {Promise<RestartPreview>} */
  preview: () => api('/api/host/restart'),
  /** @returns {Promise<RestartPreview>} */
  request: () => api('/api/host/restart', { method: 'POST' }),
  /** Login sessions belong to the old process; only read public readiness here. */
  async ready() {
    const response = await fetch('/api/host/ready', { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(3000) })
    if (!response.ok) throw new Error(`连接状态 HTTP ${response.status}`)
    const value = await response.json()
    if (typeof value.pid !== 'number' || typeof value.started_at !== 'number') throw new Error(`宿主进程响应无效：${JSON.stringify(value)}`)
    return value
  },
}
