import { api, queryString } from '../../api.js'

/**
 * @typedef {'workspace'|'inputs'|'deliveries'|'runtime'|'shared'} ResourceScope
 * @typedef {{scope:ResourceScope, task_id:number|null, path:string, file_id?:number|null}} ResourceRef
 * @typedef {{name:string, kind:string, size:number|null, modified:number, purpose:string, mime_type:string, preview:'text'|'image'|'pdf'|'download', exists:boolean, reference:ResourceRef, note:string|null, upload:Object|null}} ResourceEntry
 * @typedef {{scene:string, scope:ResourceScope, task_id:number|null, path:string, exists:boolean, entries:ResourceEntry[], next_offset:number|null}} ResourceListing
 */
const url = (action, scene, reference, extra = {}) => `/api/host/resources${action}?${queryString({ scene, ...reference, ...extra })}`

export const resourcesApi = {
  /** @param {string} scene @param {ResourceRef} location @param {number} [offset] @returns {Promise<ResourceListing>} */
  list: (scene, location, offset = 0) => api(url('', scene, location, { offset, limit: 100 })),
  /** @param {string} scene @param {ResourceRef} reference @param {number} [offset] */
  text: (scene, reference, offset = 0) => api(url('/text', scene, reference, { offset })),
  /** @param {string} scene @param {ResourceRef} reference @returns {Promise<Blob>} */
  preview: (scene, reference) => api(url('/content', scene, reference, { preview: true }), {}, 'blob'),
  downloadUrl: (scene, reference) => url('/content', scene, reference),
  /** @param {string} scene @param {{reference:ResourceRef, requester:string, name:string, note:string}} body */
  register: (scene, body) => api(`/api/host/resources/register?${queryString({ scene })}`, { method: 'POST', body: JSON.stringify(body) }),
}
