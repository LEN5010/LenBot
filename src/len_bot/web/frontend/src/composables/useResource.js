import { onMounted, onScopeDispose, ref } from 'vue'

// One readable thing on a page: its last value, the error of the last read,
// and whether a read is running. A response that arrives after a newer read
// started, or after the page closed, is dropped.
export function useResource(load, { immediate = true } = {}) {
  const data = ref(null), error = ref(null), loading = ref(false)
  let current = 0, disposed = false
  onScopeDispose(() => { disposed = true; ++current })
  async function reload(...args) {
    const own = ++current
    loading.value = true
    try {
      const value = await load(...args)
      if (disposed || own !== current) return undefined
      data.value = value
      error.value = null
      return value
    } catch (problem) {
      if (!disposed && own === current) error.value = problem
      return undefined
    } finally {
      if (!disposed && own === current) loading.value = false
    }
  }
  if (immediate) onMounted(reload)
  return { data, error, loading, reload }
}

// One user action (save, delete, run). The error stays on the page until the
// next attempt; the draft is left untouched either way.
export function useAction() {
  const busy = ref(false), error = ref(null)
  let active = 0
  async function run(action, current = () => true) {
    const own = ++active
    busy.value = true
    error.value = null
    try {
      const result = await action()
      return current() ? result : undefined
    } catch (problem) {
      if (current()) error.value = problem
      return undefined
    } finally {
      if (own === active) busy.value = false
    }
  }
  return { busy, error, run }
}
