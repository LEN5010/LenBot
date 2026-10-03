// Dedicated account-browser configuration, pairing and explicit task-session release.
import { api, queryString } from '../../api.js'

/**
 * @typedef {{socket:string, binary:string, home:string, browser_instance_id:string|null, timeout_seconds:number, max_response_bytes:number}} BrowserSettings
 * @typedef {{running:BrowserSettings|null, saved:BrowserSettings|null, restart_required:boolean, occupied:import('./tasks.js').TaskRecord[]}} BrowserState
 * @typedef {{scene:string, task_id:number, session_id:string|null}} ReleaseBrowser
 * @typedef {{browser:Object|null,session:Object|null}} TaskBrowserConnection
 */
export const browserApi = {
  /** @returns {Promise<BrowserState>} */
  read: () => api('/api/host/browser'),

  /** @param {BrowserSettings|null} settings @returns {Promise<BrowserState>} */
  save: settings => api('/api/host/browser', { method: 'PUT', body: JSON.stringify({ settings }) }),

  status: () => api('/api/host/browser/status', { method: 'POST' }),
  /** @param {string} scene @param {number} id @returns {Promise<TaskBrowserConnection>} */
  taskStatus: (scene, id) => api(`/api/host/tasks/${id}/browser/status?${queryString({ scene })}`, { method: 'POST' }),
  devices: () => api('/api/host/browser/devices'),
  pair: () => api('/api/host/browser/pair', { method: 'POST' }),
  revoke: id => api(`/api/host/browser/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  /** @param {ReleaseBrowser} body @returns {Promise<{released:boolean}>} */
  release: body => api('/api/host/browser/release', { method: 'POST', body: JSON.stringify(body) }),
}
