import { reactive } from 'vue'
import { api, sceneTitles } from '../api.js'

export const host = reactive({ state: null, stateError: null, restart: null, toast: '', operator: '', titleErrors: {},
  overview: null, overviewError: null, members: {}, updates: null })
let updatesRead = 0, stateRead = 0, restartRead = 0, titlesRead = 0, overviewRead = 0

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

export async function readSceneTitles() {
  const own = ++titlesRead
  try {
    const value = await api('/api/host/scene-titles')
    if (own !== titlesRead) return
    Object.assign(sceneTitles, value.titles)
    host.members = value.members
    host.titleErrors = value.errors
  } catch (error) {
    if (own === titlesRead) host.titleErrors = { '': error.message }
  }
}

export async function readOverview() {
  const own = ++overviewRead
  try {
    const value = await api('/api/host/overview')
    if (own !== overviewRead) return
    host.overview = value
    host.overviewError = null
  } catch (error) {
    if (own === overviewRead) host.overviewError = error
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
  ++overviewRead
  Object.assign(host, { state: null, stateError: null, restart: null, toast: '', operator: '', titleErrors: {},
    overview: null, overviewError: null, members: {} })
  for (const key of Object.keys(sceneTitles)) delete sceneTitles[key]
}

export async function readUpdates() {
  const own = ++updatesRead
  try {
    const value = await api('/api/host/updates')
    if (own === updatesRead) host.updates = value
  } catch {
    if (own === updatesRead) host.updates = null
  }
}
