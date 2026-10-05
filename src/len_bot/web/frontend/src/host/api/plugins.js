import { api } from '../../api.js'

/**
 * @typedef {{name:string,title:string,description:string,authors:string[],license:string,version:string,interface:number,category:string,capabilities:string[],usage:string[],install:'builtin'|'git',repository:string|null,homepage:string|null,ref:string|null}} CatalogEntry
 * @typedef {{source:'builtin'|'remote',url:string|null,loaded_at:number|null,entries:CatalogEntry[]}} CatalogView
 */
export const pluginsApi = {
  read: () => api('/api/host/plugins'),
  install: (url, ref = null, switch_source = false) => api('/api/host/plugins/install', { method: 'POST', body: JSON.stringify({ url, ref, switch_source }) }),
  importZip: (file, switchSource = false) => {
    const body = new FormData()
    body.append('file', file)
    body.append('switch_source', String(switchSource))
    return api('/api/host/plugins/zip', { method: 'POST', body })
  },
  update: (name, ref = null) => api(`/api/host/plugins/${encodeURIComponent(name)}/update`, { method: 'POST', body: JSON.stringify({ ref }) }),
  /** @returns {Promise<CatalogView>} */
  catalog: () => api('/api/host/plugin-catalog'),
  /** @param {string|null} url @returns {Promise<CatalogView>} */
  catalogSource: url => api('/api/host/plugin-catalog', { method: 'PUT', body: JSON.stringify({ url }) }),
  /** @returns {Promise<CatalogView>} */
  refreshCatalog: () => api('/api/host/plugin-catalog/refresh', { method: 'POST' }),
}
