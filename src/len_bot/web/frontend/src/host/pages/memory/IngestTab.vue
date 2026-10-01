<script setup>
import { computed } from 'vue'
import { api } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { developerDetails } from '../../../composables/useDeveloperMode.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'
import EmbeddingCalls from './EmbeddingCalls.vue'

const props = defineProps({ scene: { type: String, required: true } })
const ingest = useResource(() => api('/api/host/memory/ingest'))
const act = useAction()
const current = computed(() => ingest.data.value?.scenes.find(item => item.scene === props.scene) || null)
const latest = computed(() => current.value?.latest || null)
const usable = computed(() => current.value && !current.value.worker_error)
const statusLabel = { queued: '排队中', running: '整理中', submitted: '已交给记忆服务处理', complete: '完成', failed: '失败', interrupted: '中断了' }
const actions = computed(() => {
  if (!usable.value) return []
  const job = latest.value
  const list = []
  if (job === null || job.status === 'complete') list.push(['run', '现在整理一次'])
  if (job?.backend === 'local' && ['failed', 'interrupted'].includes(job.status)) list.push(['retry', '重新整理这一批'])
  if (job?.backend === 'openviking' && ['submitted', 'failed'].includes(job.status) && job.details?.task_id) list.push(['refresh', '查看处理结果'])
  return list
})
async function run(action) {
  const result = await act.run(() => api(`/api/host/memory/ingest/${encodeURIComponent(props.scene)}/${action}`, { method: 'POST' }))
  if (!result) return
  notify(action === 'run' ? '已开始检查，有足够的新消息才会整理' : action === 'retry' ? '已重新排队' : '已请求结果')
  ingest.reload()
}
</script>

<template>
  <section class="surface ingest">
    <div class="head">
      <div><h2>后台整理</h2><p class="muted">攒够一批新消息后，Bot 会在后台把值得记住的内容整理进记忆。</p></div>
      <v-btn size="small" variant="text" :loading="ingest.loading.value" @click="ingest.reload()">刷新</v-btn>
    </div>
    <ErrorNote v-if="ingest.error.value" title="读取整理状态失败" :error="ingest.error.value" />
    <template v-if="ingest.data.value">
      <p v-if="!ingest.data.value.enabled" class="muted">后台整理没有开启，可以在设置里打开。</p>
      <p v-else-if="!current" class="muted">这个群没有参与后台整理。</p>
      <template v-else>
        <ErrorNote v-if="current.worker_error" title="这个群的后台整理停止了，重启 LenBot 后恢复" :error="current.worker_error" />
        <div v-if="latest" class="job">
          <strong>最近一次：{{ statusLabel[latest.status] || latest.status }}</strong>
          <span class="muted">{{ formatTime(latest.started) }}{{ latest.ended ? ` — ${formatTime(latest.ended)}` : '' }}</span>
          <ErrorNote v-if="latest.error" title="这次整理出错了" :error="latest.error" />
        </div>
        <p v-else class="muted">还没有整理过。开启后只整理之后的新消息。</p>
        <div v-if="actions.length" class="actions">
          <v-btn v-for="[key, label] in actions" :key="key" variant="outlined" :loading="act.busy.value" @click="run(key)">{{ label }}</v-btn>
        </div>
        <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
        <DevOnly label="整理记录详情"><pre>{{ JSON.stringify(current, null, 2) }}</pre></DevOnly>
      </template>
    </template>
  </section>
  <EmbeddingCalls v-if="developerDetails" :scene="scene" />
</template>

<style scoped>
.ingest{display:grid;gap:12px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.job{display:grid;gap:4px}
.actions{display:flex;gap:8px;flex-wrap:wrap}
</style>
