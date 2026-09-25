import { reactive } from 'vue'
import { api, setDisplayTimezone } from '../api.js'

const state = reactive({
  status: null,
  scenes: [],
  error: '',
  sceneError: '',
  discoveryError: '',
  sceneReadAt: null,
  loading: false,
  loadedScenes: false
})
let request = 0
let sceneGeneration = 0, sceneRequest
export function useAppState() {
  return state
}
export function clearAppState() {
  ++request;
  ++sceneGeneration;
  sceneRequest = null
  Object.assign(state, {
    status: null,
    scenes: [],
    error: '',
    sceneError: '',
    discoveryError: '',
    sceneReadAt: null,
    loading: false,
    loadedScenes: false
  })
  setDisplayTimezone(null)
}
export async function refreshStatus() {
  const own = ++request
  state.loading = true
  try {
    const data = await api('/api/overview/status');
    if (own === request) {
      state.status = data;
      setDisplayTimezone(data.business_timezone);
      state.error = ''
    }
  }
  catch (error) {
    if (own === request) state.error = error.message
  }
  finally {
    if (own === request) state.loading = false
  }
}
export async function loadScopes(refresh = false) {
  if (state.loadedScenes && !refresh) return
  if (sceneRequest) return sceneRequest
  const own = ++sceneGeneration
  sceneRequest = (async () => {
    try {
      const data = await api('/api/cockpit/scenes');
      if (own !== sceneGeneration) return;
      state.scenes = data.scenes;
      state.loadedScenes = true;
      state.sceneReadAt = Date.now()/1000;
      state.sceneError = '';
      state.discoveryError = data.discovery?.error || ''
    }
    catch (error) {
      if (own === sceneGeneration) state.sceneError = error.message
    }
    finally {
      if (own === sceneGeneration) sceneRequest = null
    }
  })()
  return sceneRequest
}
