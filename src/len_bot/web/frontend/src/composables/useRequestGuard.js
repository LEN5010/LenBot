// Only the newest in-flight request may write. Every view had its own copy of
// `const own = ++requestId ... if (own !== requestId) return`; this is that,
// once, so a slow earlier response cannot overwrite fresher data.
export function useRequestGuard() {
  let current = 0
  return function begin() {
    const own = ++current
    return () => own === current
  }
}
