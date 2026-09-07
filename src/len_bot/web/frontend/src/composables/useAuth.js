import { reactive } from 'vue'
import { api } from '../api.js'

const state = reactive({ status: 'loading', user: null, error: '' })
let initialization
export function useAuth() { return state }
export function clearAuth() { state.user = null; state.status = 'unauthenticated'; state.error = '' }
export async function refreshAuth() {
  state.status = 'loading'; state.error = ''
  try { state.user = await api('/api/auth/me'); state.status = 'authenticated' }
  catch (error) {
    if (error.status === 401) clearAuth()
    else { state.status = 'error'; state.error = error.message }
  }
}
export function ensureAuth() {
  if (!initialization) initialization = refreshAuth()
  return initialization
}
export async function login(username, password) {
  const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) })
  state.user = result; state.status = 'authenticated'; state.error = ''
}
export async function logout() { await api('/api/auth/logout', { method: 'POST' }); clearAuth() }
