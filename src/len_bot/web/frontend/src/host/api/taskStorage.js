import { api, queryString } from '../../api.js'

/**
 * @typedef {'temporary'|'environment'} CleanupOperation
 * @typedef {{task_id:number,status:'complete'|'error',error:string|null,removal:Object|null}} CleanupResult
 */
export const taskStorageApi = {
  pool: () => api('/api/host/task-storage/pool'),
  list: (scene, query) => api(`/api/host/task-storage?${queryString({ scene, ...query })}`),
  /** @param {string} scene @param {{task_ids:number[],operation:CleanupOperation}} body @returns {Promise<{items:CleanupResult[]}>} */
  cleanup: (scene, body) => api(`/api/host/task-storage/cleanup?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),
  close: (scene, id) => api(`/api/host/tasks/${id}/close-environment?${queryString({ scene })}`, { method: 'POST' }),
}
