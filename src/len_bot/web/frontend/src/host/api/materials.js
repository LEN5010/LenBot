// Scene-shared materials, separate from private task input snapshots.
import { api, queryString } from '../../api.js'

/**
 * @typedef {{task_id:number, file_id:number, name:string, directory:string, confirmed:boolean}} MaterialAdoption
 * @typedef {{directory:string, exists:boolean, scene:string, files:Array<{name:string, size:number}>, notice:string}} MaterialList
 */
export const materialsApi = {
  /** @param {string} scene @returns {Promise<MaterialList>} */
  list: scene => api(`/api/host/materials?${queryString({ scene })}`),

  /** @param {string} scene @param {MaterialAdoption} body */
  adopt: (scene, body) => api(`/api/host/materials/adopt?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),
}
