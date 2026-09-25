import { ref } from 'vue'
import { api } from '../../api.js'

// Budget tab: model calls recorded against this job, read page by page.
export function useJobUsage({job, sameJob}) {
  const usage = ref(null),
    usageLoading = ref(false),
    usageError = ref(''),
    usageReadAt = ref(null),
    usagePage = ref(1)
  let usageRequest = 0
  function resetUsage() {
    ++usageRequest
    usage.value = null;
    usageLoading.value = false;
    usageError.value = '';
    usageReadAt.value = null;
    usagePage.value = 1
  }
  async function loadUsage(page = 1) {
    if (!job.value) return
    const current = job.value, request = ++usageRequest
    usageLoading.value = true;
    usageError.value = ''
    try {
      const value = await api('/api/models/usage?' + new URLSearchParams({ scene_id: current.scene_id, job_id: current.id, page, page_size: 20 }))
      if (request !== usageRequest || !sameJob(current)) return
      usage.value = value;
      usagePage.value = value.page;
      usageReadAt.value = Date.now()/1000
    } catch (error) {
      if (request === usageRequest && sameJob(current)) usageError.value = error.message
    }
    finally {
      if (request === usageRequest) usageLoading.value = false
    }
  }
  return {
    usage, usageLoading, usageError, usageReadAt, usagePage, loadUsage, resetUsage
  }
}
