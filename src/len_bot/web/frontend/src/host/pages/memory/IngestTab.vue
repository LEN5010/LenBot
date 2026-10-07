<script setup>
import { computed } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { developerDetails } from '../../../composables/useDeveloperMode.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import DevOnly from '../../ui/DevOnly.vue'
import EmbeddingCalls from './EmbeddingCalls.vue'

const props = defineProps({ scene: { type: String, required: true } })
const ingest = useResource(() => api('/api/host/memory/ingest'))
const act = useAction()
const current = computed(() => ingest.data.value?.scenes.find(item => item.scene === props.scene) || null)
const latest = computed(() => current.value?.latest || null)
const usable = computed(() => current.value && !current.value.worker_error)
const actions = computed(() => {
  if (!usable.value) return []
  const job = latest.value
  const list = []
  if (job === null || job.status === 'complete') list.push(['run', '现在整理一次'])
  if (job?.backend === 'local' && ['failed', 'interrupted'].includes(job.status)) list.push(['retry', '重新整理这一批'])
  return list
})
async function run(action) {
  const result = await act.run(() => api(`/api/host/memory/ingest/${encodeURIComponent(props.scene)}/${action}`, { method: 'POST' }))
  if (!result) return
  notify(action === 'run' ? '已开始检查，有足够的新消息才会整理' : '已重新排队')
  ingest.reload()
}
</script>

<template>
  <Panel title="后台整理">
    <template #actions><v-btn size="small" variant="text" :loading="ingest.loading.value" @click="ingest.reload()">刷新</v-btn></template>
    <ResourceState :resource="ingest" error-title="读取整理状态失败" v-slot="{ data }">
      <p v-if="!data.enabled" class="muted">后台整理没有开启，可以在设置里打开。</p>
      <p v-else-if="!current" class="muted">这个群没有参与后台整理。</p>
      <template v-else>
        <ErrorNote v-if="current.worker_error" title="这个群的后台整理停止了，重启 LenBot 后恢复" :error="current.worker_error" />
        <div v-if="latest" class="inline">
          <span>最近一次</span><StatusBadge kind="ingest" :value="latest.status" />
          <span class="muted small">{{ formatTime(latest.started) }}{{ latest.ended ? ` — ${formatTime(latest.ended)}` : '' }}</span>
        </div>
        <ErrorNote v-if="latest?.error" title="这次整理出错了" :error="latest.error" />
        <p v-if="!latest" class="muted">还没有整理过。开启后只整理之后的新消息。</p>
        <div v-if="actions.length" class="inline">
          <v-btn v-for="[key, label] in actions" :key="key" :variant="key === 'retry' ? 'flat' : 'outlined'" :color="key === 'retry' ? 'primary' : undefined" :loading="act.busy.value" @click="run(key)">{{ label }}</v-btn>
        </div>
        <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
        <DevOnly label="整理记录详情" :json="current" />
      </template>
    </ResourceState>
  </Panel>
  <EmbeddingCalls v-if="developerDetails" :scene="scene" />
</template>

<style scoped>
p{margin:0}
</style>
