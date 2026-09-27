import { reactive } from 'vue'
import { api, resetApiSession } from '../api.js'
import { clearSceneVisits } from './sceneVisits.js'
import { clearAppState } from './useAppState.js'

const state = reactive({ status: 'loading', user: null, error: '', panelContext: null })
let initialization
let authRequest = 0
export function useAuth() {
  return state
}
export function clearAuth() {
  ++authRequest;
  resetApiSession();
  clearSceneVisits();
  clearAppState();
  state.user = null;
  state.status = 'unauthenticated';
  state.error = ''
}
export async function refreshAuth() {
  const own = ++authRequest
  state.status = 'loading';
  state.error = ''
  try {
    if (!state.panelContext) {
      const context = await api('/api/panel-context')
      if (own !== authRequest) return
      if (!['legacy', 'isolated'].includes(context.mode) ||
          context.home !== (context.mode === 'isolated' ? '/chat-test' : '/overview')) {
        throw new Error('面板环境响应无效：缺少明确的 mode 或 home')
      }
      state.panelContext = context
    }
    const user = await api('/api/auth/me');
    if (own !== authRequest) return;
    state.user = user;
    state.status = 'authenticated'
  }
  catch (error) {
    if (own !== authRequest) return
    if (error.status === 401) clearAuth()
    else {
      state.status = 'error';
      state.error = error.message
    }
  }
}
export function ensureAuth() {
  if (!initialization) initialization = refreshAuth()
  return initialization
}
export async function login(username, password) {
  const own = ++authRequest
  const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
  if (own !== authRequest) return false
  resetApiSession();
  clearSceneVisits();
  clearAppState()
  state.user = result;
  state.status = 'authenticated';
  state.error = ''
  return true
}
export async function logout() {
  await api('/api/auth/logout', { method: 'POST' });
  clearAuth()
}
