import { api, queryString } from '../../api.js'

/**
 * @typedef {{when:string, note:string, for:string}} ScheduleInput
 * @typedef {{id:number, scene:string, created:number, due_at:number, timezone:string, note:string, target:string, requester:string|null, status:string, delivered_at:number|null, reason:string|null, interval_seconds:number|null, cron:string|null, legacy_source:Object|null}} Schedule
 */
export const schedulesApi = {
  state: () => api('/api/host/schedules/state'),

  /** @param {string} scene @param {{status:string, offset:number, limit:number}} query @returns {Promise<{items:Schedule[], next_offset:number|null}>} */
  list: (scene, query) => api(`/api/host/schedules?${queryString({ scene, ...query })}`),

  /** @param {string} scene @param {ScheduleInput} body @returns {Promise<Schedule>} */
  create: (scene, body) => api(`/api/host/schedules?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),

  /** @param {string} scene @param {number} id @returns {Promise<Schedule>} */
  cancel: (scene, id) => api(`/api/host/schedules/${id}/cancel?${queryString({ scene })}`, { method: 'POST' }),

  /** @param {string} scene @param {number} offset */
  proactive: (scene, offset) => api(`/api/host/schedules/proactive?${queryString({ scene, offset })}`),
}
