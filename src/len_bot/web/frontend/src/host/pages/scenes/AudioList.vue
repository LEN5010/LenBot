<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import LoadMore from '../../ui/LoadMore.vue'
import DevOnly from '../../ui/DevOnly.vue'
import CodeBlock from '../../ui/CodeBlock.vue'

const props = defineProps({ scene: { type: String, required: true } })
const root = `/api/host/scenes/${encodeURIComponent(props.scene)}/audio`
const rowPath = row => `${root}/${encodeURIComponent(row.platform_id)}/${row.audio_index}`
const rows = ref([])
const list = useResource(async (offset = 0) => {
  const value = await api(`${root}?offset=${offset}`)
  return { ...value, offset }
})
watch(() => list.data.value, value => {
  if (value) rows.value = value.offset ? [...rows.value, ...value.items] : value.items
})
const action = useAction(), callRead = useAction()
const calls = ref({})
const view = computed(() => list.data.value)

async function transcribe(row) {
  const refresh = row.wav_bytes !== null
  if (!await confirm({ title: '重新转写这段语音？', text: '会调用一次语音识别模型。', confirmLabel: '转写' })) return
  await action.run(() => api(`${rowPath(row)}/transcribe`, { method: 'POST', body: JSON.stringify({ refresh }) }))
  list.reload()
}
async function showCalls(row) {
  const value = await callRead.run(() => api(`${rowPath(row)}/calls`))
  if (value) calls.value = { ...calls.value, [rowPath(row)]: value }
}
</script>

<template>
  <Panel v-if="rows.length || list.error.value" title="语音消息"
    :description="view && !view.available ? '还没有配置语音识别模型，可以在模型页添加。' : ''">
    <ErrorNote v-if="list.error.value" title="读取语音记录失败" :error="list.error.value" @retry="list.reload()" />
    <ErrorNote v-if="action.error.value" title="转写没有完成" :error="action.error.value" />
    <ErrorNote v-if="callRead.error.value" title="读取模型调用记录失败" :error="callRead.error.value" />
    <ErrorNote v-if="view?.worker_error" title="语音转写已停止" :error="view.worker_error" />
    <ObjectList divided>
      <li v-for="row in rows" :key="rowPath(row)" class="audio-row">
        <div class="inline"><StatusBadge kind="audio" :value="row.status" />
          <span class="muted small">{{ formatTime(row.updated, view.timezone) }}<template v-if="row.duration !== null"> · {{ row.duration.toFixed(1) }} 秒</template></span>
          <v-btn class="ml-auto" size="small" variant="text" :loading="action.busy.value"
            :disabled="!view.available || view.stopping || ['queued', 'running'].includes(row.status)" @click="transcribe(row)">重新转写</v-btn></div>
        <audio v-if="row.wav_bytes !== null" :src="`${rowPath(row)}/wav`" controls preload="none" />
        <p v-if="row.transcript !== null" class="transcript">{{ row.transcript || '没有识别出文字' }}</p>
        <ErrorNote v-if="row.error" title="这段语音转写失败" :error="row.error" />
        <DevOnly>
          <v-btn size="small" variant="text" @click="showCalls(row)">模型调用记录</v-btn>
          <CodeBlock v-if="calls[rowPath(row)]" :text="JSON.stringify(calls[rowPath(row)], null, 2)" />
        </DevOnly>
      </li>
    </ObjectList>
    <LoadMore v-if="view && view.next_offset !== null" label="更早的语音" :loading="list.loading.value" @more="list.reload(view.next_offset)" />
  </Panel>
</template>

<style scoped>
.audio-row{padding:var(--sp-3) 0;display:grid;gap:var(--sp-2)}
audio{width:min(100%,420px)}
.transcript{white-space:pre-wrap;margin:0}
</style>
