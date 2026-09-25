// One read that answers to a request guard. Each call takes the guard, clears
// the error and sets loading; only a response that is still fresh is applied,
// and only a still-fresh call reports its error or clears loading. The error
// shown is the thrown message itself. apply may throw to reject a response.
export function useGuardedRead(guard, loading, error) {
  return async function read(request, apply) {
    const fresh = guard()
    loading.value = true
    error.value = ''
    try {
      const result = await request()
      if (fresh()) apply(result)
    } catch (e) {
      if (fresh()) error.value = e.message
    } finally {
      if (fresh()) loading.value = false
    }
  }
}
