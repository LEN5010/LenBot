import { onScopeDispose } from 'vue'

export function useRequestGuard(selection = () => undefined) {
  let current = 0
  let disposed = false
  onScopeDispose(() => { disposed = true; ++current })
  return function begin() {
    const own = ++current, key = selection()
    return () => !disposed && own === current && key === selection()
  }
}
