import { reactive } from 'vue'
import { api } from '../api.js'

const state = reactive({ status: null, scenes: [], error: '', sceneError: '', loading: false, loadedScenes: false })
let request = 0
let sceneRequest
export function useAppState() { return state }
export async function refreshStatus() {
  const own = ++request
  state.loading = true
  try { const data = await api('/api/overview/status'); if (own === request) { state.status = data; state.error = '' } }
  catch (error) { if (own === request) state.error = error.message }
  finally { if (own === request) state.loading = false }
}
export async function loadScopes(refresh = false) {
  if (state.loadedScenes && !refresh) return
  if (sceneRequest) return sceneRequest
  sceneRequest = (async () => {
    try { const data = await api('/api/cockpit/scenes'); state.scenes = data.scenes; state.loadedScenes = true; state.sceneError = '' }
    catch (error) { state.sceneError = error.message }
    finally { sceneRequest = null }
  })()
  return sceneRequest
}
