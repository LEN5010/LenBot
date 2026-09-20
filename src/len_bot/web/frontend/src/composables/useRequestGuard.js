import { onScopeDispose } from 'vue'

// A response belongs to its read selection and component lifetime, not just
// its place among requests. This does not cancel an accepted server operation.
export function useRequestGuard(selection = () => undefined) {
  let current = 0
  let disposed = false
  onScopeDispose(() => { disposed = true; ++current })
  return function begin() {
    const own = ++current, key = selection()
    return () => !disposed && own === current && key === selection()
  }
}
