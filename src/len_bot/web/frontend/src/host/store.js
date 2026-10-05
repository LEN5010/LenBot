// Host-wide facts shared by the shell and every page: one read of the host
// state and of what needs a restart, refreshed on navigation and after saves.
// `operator` is the 平台账号 of the person using the panel, which task and reminder
// permissions are checked against; it is kept while moving between pages.
import { reactive } from 'vue'
import { api, sceneTitles } from '../api.js'

export const host = reactive({ state: null, stateError: null, restart: null, toast: '', operator: '', titleErrors: {} })
let stateRead = 0, restartRead = 0, titlesRead = 0

export async function readHostState() {
  const own = ++stateRead
  try {
    const value = await api('/api/host/state')
    if (own !== stateRead) return
    host.state = value
    host.stateError = null
  } catch (error) {
    if (own === stateRead) host.stateError = error
  }
}

// Group names come from 平台账号 once the connection is up; failures stay visible
// in developer mode and the scene keeps showing its number.
export async function readSceneTitles() {
  const own = ++titlesRead
  try {
    const value = await api('/api/host/scene-titles')
    if (own !== titlesRead) return
    Object.assign(sceneTitles, value.titles)
    host.titleErrors = value.errors
  } catch (error) {
    if (own === titlesRead) host.titleErrors = { '': error.message }
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
  ++titlesRead
  Object.assign(host, { state: null, stateError: null, restart: null, toast: '', operator: '', titleErrors: {} })
  for (const key of Object.keys(sceneTitles)) delete sceneTitles[key]
}
