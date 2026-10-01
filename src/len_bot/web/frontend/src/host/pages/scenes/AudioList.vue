<script setup>
import { computed, ref } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}/audio`
const rowPath = row => `${root}/${encodeURIComponent(row.platform_id)}/${row.audio_index}`
const rows = ref([])
const list = useResource(async (offset = 0) => {
  const value = await api(`${root}?offset=${offset}`)
  rows.value = offset ? [...rows.value, ...value.items] : value.items
  return value
})
const action = useAction(), callRead = useAction()
const calls = ref({})
const statusLabels = { idle: '未转写', queued: '排队中', running: '转写中', complete: '已转写', failed: '转写失败', interrupted: '已中断' }
const view = computed(() => list.data.value)

async function transcribe(row) {
  const refresh = row.wav_bytes !== null
  if (!window.confirm('重新转写这段语音？会调用一次语音识别模型。')) return
  await action.run(() => api(`${rowPath(row)}/transcribe`, { method: 'POST', body: JSON.stringify({ refresh }) }))
  list.reload()
}
async function showCalls(row) {
  const value = await callRead.run(() => api(`${rowPath(row)}/calls`))
  if (value) calls.value = { ...calls.value, [rowPath(row)]: value }
}
</script>

<template>
  <section v-if="rows.length || list.error.value" class="surface">
    <h2>语音消息</h2>
    <p v-if="view && !view.available" class="muted">还没有配置语音识别模型，可以在模型页添加。</p>
    <ErrorNote v-if="list.error.value" title="读取语音记录失败" :error="list.error.value" />
    <ErrorNote v-if="action.error.value" title="转写没有完成" :error="action.error.value" />
    <ErrorNote v-if="callRead.error.value" title="读取模型调用记录失败" :error="callRead.error.value" />
    <ErrorNote v-if="view?.worker_error" title="语音转写已停止" :error="view.worker_error" />
    <article v-for="row in rows" :key="rowPath(row)" class="audio-row">
      <div class="audio-head"><strong>{{ statusLabels[row.status] || row.status }}</strong>
        <span class="muted">{{ formatTime(row.updated, view.timezone) }}<template v-if="row.duration !== null"> · {{ row.duration.toFixed(1) }} 秒</template></span></div>
      <audio v-if="row.wav_bytes !== null" :src="`${rowPath(row)}/wav`" controls preload="none" />
      <p v-if="row.transcript !== null" class="transcript">{{ row.transcript || '没有识别出文字' }}</p>
      <ErrorNote v-if="row.error" title="这段语音转写失败" :error="row.error" />
      <v-btn size="small" variant="outlined" :loading="action.busy.value"
        :disabled="!view.available || view.stopping || ['queued', 'running'].includes(row.status)" @click="transcribe(row)">重新转写</v-btn>
      <DevOnly>
        <v-btn size="small" variant="text" @click="showCalls(row)">模型调用记录</v-btn>
        <pre v-if="calls[rowPath(row)]">{{ JSON.stringify(calls[rowPath(row)], null, 2) }}</pre>
      </DevOnly>
    </article>
    <v-btn v-if="view?.next_offset !== null && view" variant="text" :loading="list.loading.value" @click="list.reload(view.next_offset)">更早的语音</v-btn>
  </section>
</template>

<style scoped>
.audio-row{border-top:1px solid var(--line);padding:12px 0;display:grid;gap:8px;justify-items:start}
.audio-head{display:flex;gap:12px;align-items:baseline}
audio{width:min(100%,420px)}
.transcript{white-space:pre-wrap;margin:0}
</style>
