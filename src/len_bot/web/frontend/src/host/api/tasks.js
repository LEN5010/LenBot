// Task requests and file URLs. Components own drafts and presentation.
import { api, queryString } from '../../api.js'

/**
 * @typedef {'queued'|'running'|'waiting_input'|'done'|'failed'|'cancelled'} TaskStatus
 * @typedef {Object} TaskRecord
 * @property {number} id
 * @property {string} scene
 * @property {string} requester
 * @property {string} goal
 * @property {string} deliverable
 * @property {string} context
 * @property {string} input
 * @property {string[]} materials
 * @property {TaskStatus} status
 * @property {number} created
 * @property {number|null} started
 * @property {number|null} ended
 * @property {string|null} container
 * @property {Object|null} question Native Pi question or browser handoff.
 * @property {string|null} summary
 * @property {string|null} error
 * @property {boolean} account_browser
 * @property {boolean} browser_active
 * @property {string|null} browser_session
 * @typedef {{id:number, task_id:number, name:string, size:number, note:string|null, status:'registered', upload:Object|null}} RegisteredFile
 * @typedef {{id:number, kind:string, created:number, delivered_at:number|null, event_type:string|null, tool_name:string|null, browser_method:string|null, preview:string, truncated:boolean}} TaskEventPreview
 * @typedef {{task:TaskRecord & {workspace_discard_requested:boolean, active_timeout_seconds:number|null}, events:TaskEventPreview[], network:Object|null, next_after:number, files:RegisteredFile[]}} TaskDetail
 * @typedef {{requester:string, goal:string, deliverable:string, context:string, account_browser:boolean, materials:string[]}} DelegateInput
 * @typedef {{action:'append'|'continue'|'answer'|'cancel', id:number, requester:string, text?:string, confirmed?:boolean, question_id?:string}} TaskActionInput
 * @typedef {{requester:string, workspace:string, runtime:string, confirmed:boolean}} DiscardInput
 */

const taskUrl = (scene, id, suffix = '', query = {}) => `/api/host/tasks/${id}${suffix}?${queryString({ scene, ...query })}`

export const tasksApi = {
  state: () => api('/api/host/tasks/state'),

  /** @param {string} scene @param {{status:string, offset:number, limit:number}} query @returns {Promise<{items:TaskRecord[], next_offset:number|null}>} */
  list: (scene, query) => api(`/api/host/tasks?${queryString({ scene, ...query })}`),

  /** @param {string} scene @param {number} id @param {{after?:number, limit?:number}} [page] @returns {Promise<TaskDetail>} */
  detail: (scene, id, page = {}) => api(taskUrl(scene, id, '', { after: 0, limit: 100, ...page })),

  /** @param {string} scene @param {number} id @param {number} event */
  event: (scene, id, event) => api(taskUrl(scene, id, `/events/${event}`)),

  /** @param {string} scene @param {DelegateInput} body @returns {Promise<TaskRecord>} */
  delegate: (scene, body) => api(`/api/host/tasks/delegate?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),

  /** @param {string} scene @param {TaskActionInput} body */
  action: (scene, body) => api(`/api/host/tasks/action?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),

  /** @param {string} scene @param {number} id @param {number} file @returns {Promise<Blob>} */
  download: (scene, id, file) => api(taskUrl(scene, id, `/files/${file}`), {}, 'blob'),

  /** @param {string} scene @param {number} id */
  storage: (scene, id) => api(taskUrl(scene, id, '/storage')),

  /** @param {string} scene @param {number} id @param {DiscardInput} body */
  discard: (scene, id, body) => api(taskUrl(scene, id, '/workspace-discard'), { method: 'POST', body: JSON.stringify(body) }),

  skills: (scene, id) => api(taskUrl(scene, id, '/skills')),
  sessionUrl: (scene, id) => taskUrl(scene, id, '/session'),
  exportUrl: (scene, id) => taskUrl(scene, id, '/export'),
  liveUrl: (scene, id) => `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${taskUrl(scene, id, '/live')}`,
}
