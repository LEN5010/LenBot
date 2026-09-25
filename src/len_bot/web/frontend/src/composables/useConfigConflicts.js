import { reactive } from 'vue'

// The settings tabs keep separate in-memory drafts. A conflict needs a fresh
// read for that same tab before the operator can choose a new baseline. This
// holds only UI facts; callers retain their request lifetime and save contract.
export function useConfigConflicts() {
  const entries = reactive({})
  function mark(domain, problem) {
    if (problem.status !== 409 || problem.details == null || problem.details?.config_saved === true) return false
    entries[domain] = {
      problem: {
        message: problem.message,
        path: Array.isArray(problem.details?.path) ? problem.details.path : []
      },
      snapshot: null,
      readAt: null,
      readError: ''
    }
    return true
  }
  function beginRead(domain) {
    const entry = entries[domain]
    if (entry) {
      entry.snapshot = null;
      entry.readAt = null;
      entry.readError = ''
    }
  }
  function capture(domain, snapshot) {
    const entry = entries[domain]
    if (!entry) return false
    entry.snapshot = snapshot;
    entry.readAt = Date.now()/1000;
    entry.readError = ''
    return true
  }
  function readFailed(domain, problem) {
    if (entries[domain]) entries[domain].readError = problem.message
  }
  function clear(domain) {
    delete entries[domain]
  }
  return { entries, mark, beginRead, capture, readFailed, clear }
}
