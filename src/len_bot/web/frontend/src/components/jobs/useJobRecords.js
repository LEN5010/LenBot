import { ref } from 'vue'
import { api } from '../../api.js'

// Records tab: traces, calls, events and actions related to this job.
export function useJobRecords({job, sameJob}) {
  const records = ref(null), recordsLoading = ref(false), recordsError = ref('')
  let recordsRequest = 0
  function resetRecords() {
    ++recordsRequest;
    records.value = null;
    recordsLoading.value = false;
    recordsError.value = ''
  }
  async function loadRecords() {
    if (!job.value) return
    const current = job.value, request = ++recordsRequest
    recordsLoading.value = true;
    recordsError.value = ''
    try {
      const value = await api('/api/cockpit/relations?' + new URLSearchParams({ scene_id: current.scene_id, job_id: current.id }))
      if (request === recordsRequest && sameJob(current)) records.value = value
    } catch (error) {
      if (request === recordsRequest && sameJob(current)) recordsError.value = error.message
    }
    finally {
      if (request === recordsRequest) recordsLoading.value = false
    }
  }
  return {
    records, recordsLoading, recordsError, loadRecords, resetRecords
  }
}
