// Host-wide facts shared by the shell and every page: one read of the host
// state and of what needs a restart, refreshed on navigation and after saves.
import { reactive } from 'vue'
import { api, setDisplayTimezone } from '../api.js'

export const host = reactive({ state: null, stateError: null, restart: null, toast: '' })
let stateRead = 0, restartRead = 0

export async function readHostState() {
  const own = ++stateRead
  try {
    const value = await api('/api/host/state')
    if (own !== stateRead) return
    host.state = value
    host.stateError = null
    setDisplayTimezone(value.timezone)
  } catch (error) {
    if (own === stateRead) host.stateError = error
  }
}

export async function readPendingRestart() {
  const own = ++restartRead
  try {
    const value = await api('/api/host/pending-restart')
    if (own === restartRead) host.restart = value
  } catch (error) {
    if (own === restartRead) host.restart = { error }
  }
}

export function notify(text) {
  host.toast = text
}

export function clearHost() {
  ++stateRead
  ++restartRead
  Object.assign(host, { state: null, stateError: null, restart: null, toast: '' })
}
