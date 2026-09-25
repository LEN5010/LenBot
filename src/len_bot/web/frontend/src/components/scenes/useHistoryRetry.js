import { ref } from 'vue'
import { api, queryString } from '../../api.js'

// Retrying one history batch from the history tab, and following that retry
// while the page stays on the same scene's history view.
export function useHistoryRetry({sceneId, tab, loadDetail, loadAux}) {
  const retryConfirmation = ref(null),
    retrySaving = ref(false),
    retryError = ref(''),
    feedback = ref(''),
    retryingBatch = ref(null)
  let retryPoll = null
  let retryGeneration = 0
  function askRetry(batch) {
    retryConfirmation.value = {
      id: batch.id,
      scene: sceneId.value,
      range: `${batch.start_rowid}:${batch.start_offset} → ${batch.end_rowid}:${batch.end_offset}`,
      error: batch.failure_detail || batch.error_type
    };
    retryError.value = ''
  }
  function stopRetryPoll() {
    if (retryPoll) {
      clearInterval(retryPoll);
      retryPoll = null
    }
    retryingBatch.value = null
  }
  async function pollRetry(id, scene, generation) {
    const current = () => generation === retryGeneration && sceneId.value === scene && tab.value === 'history'
    if (!current() || retryingBatch.value !== id) return
    try {
      const batch = await api(`/api/cockpit/history-batches/${encodeURIComponent(id)}?${queryString({ scene_id: scene })}`)
      if (!current() || retryingBatch.value !== id) return
      if (batch.status === 'pending') return
      stopRetryPoll();
      await Promise.all([loadDetail(), loadAux()])
      if (!current()) return
      if (batch.status === 'completed') feedback.value = '此区间已完成历史摘要覆盖。'
      else retryError.value = batch.failure_detail || batch.error_type || '此区间处理失败，请查看失败详情后再重试。'
    } catch (error) {
      if (current() && retryingBatch.value === id) retryError.value = error.message
    }
  }
  async function retryHistory() {
    if (!retryConfirmation.value || retrySaving.value) return
    stopRetryPoll()
    const generation = ++retryGeneration
    const pending = { ...retryConfirmation.value };
    retrySaving.value = true;
    retryError.value = ''
    const current = () => generation === retryGeneration && sceneId.value === pending.scene && tab.value === 'history'
    try {
      const result = await api(`/api/cockpit/history-batches/${encodeURIComponent(pending.id)}/retry`, { method: 'POST', body: JSON.stringify({ scene_id: pending.scene }) })
      if (!current()) return
      feedback.value = result.message || '已提交此区间的维护重试，正在等待处理结果。';
      retryConfirmation.value = null;
      retryingBatch.value = pending.id
      retryPoll = setInterval(() => pollRetry(pending.id, pending.scene, generation), 1500)
      await pollRetry(pending.id, pending.scene, generation)
      if (current() && retryingBatch.value === pending.id) await Promise.all([loadDetail(), loadAux()])
    } catch (error) {
      if (current()) retryError.value = error.message
    }
    finally {
      if (current()) retrySaving.value = false
    }
  }
  // Leaving the view abandons the confirmation and stops following a submitted
  // retry; the retry itself is not cancelled.
  function cancelRetry() {
    ++retryGeneration;
    stopRetryPoll();
    retrySaving.value = false
    retryConfirmation.value = null;
    retryError.value = '';
    feedback.value = ''
  }
  function disposeRetry() {
    ++retryGeneration;
    stopRetryPoll();
  }
  return {
    retryConfirmation, retrySaving, retryError, feedback, askRetry, stopRetryPoll, retryHistory,
    cancelRetry, disposeRetry
  }
}
