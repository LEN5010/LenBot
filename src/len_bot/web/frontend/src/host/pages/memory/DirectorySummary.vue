<script setup>
// The summary of the open directory: the local backend writes its own summaries,
// OpenViking keeps a native overview for memory directories.
import { computed } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const props = defineProps({
  scene: { type: String, required: true }, scope: { type: String, required: true },
  path: { type: String, required: true }, state: { type: Object, required: true },
})
const local = props.state.backend === 'local'
const native = !local && props.scope === 'scene'
  && (props.path === 'memories' || props.path.startsWith('memories/') || /^peers\/[1-9][0-9]*\/memories(?:\/|$)/.test(props.path))
const shown = local || native
const summary = useResource(() => local
  ? api('/api/host/memory/summary?' + queryString({ scene: props.scene, path: props.path, scope: props.scope }))
  : api('/api/host/memory/native-overview?' + queryString({ scene: props.scene, path: props.path })), { immediate: shown })
const generate = useAction()
const data = computed(() => summary.data.value)
const runLabel = { running: '进行中', complete: '完成', failed: '失败', interrupted: '中断' }

async function regenerate() {
  const result = await generate.run(() => local
    ? api('/api/host/memory/summary', { method: 'POST', body: JSON.stringify({ scene: props.scene, path: props.path, scope: props.scope }) })
    : api('/api/host/memory/native-overview', { method: 'POST', body: JSON.stringify({ scene: props.scene, path: props.path }) }))
  if (!result) return
  if (result.skipped) notify(result.skipped)
  else if (result.complete === false) notify('更新完了，但有部分内容没处理成功')
  else notify('已更新')
  summary.reload()
}
</script>

<template>
  <section v-if="shown && !(local && data && !data.enabled && data.summary.abstract === null)" class="surface summary">
    <div class="head">
      <h2>{{ local && path === '' && scope === 'scene' ? '本群画像' : '这个目录的摘要' }}</h2>
      <v-btn v-if="!local || data?.enabled" size="small" variant="outlined" :loading="generate.busy.value" @click="regenerate">重新生成</v-btn>
    </div>
    <p v-if="!local" class="muted">由记忆服务生成，重新生成会调用记忆服务的模型，可能产生费用。</p>
    <p v-else-if="data?.enabled" class="muted">重新生成会调用记忆整理模型，会产生费用。</p>
    <ErrorNote v-if="summary.error.value" title="读取摘要失败" :error="summary.error.value" />
    <ErrorNote v-if="generate.error.value" title="没有生成成功" :error="generate.error.value" />
    <template v-if="data && local">
      <p v-if="data.summary.abstract === null" class="muted">还没有摘要</p>
      <template v-else>
        <p><strong>{{ data.summary.abstract }}</strong></p>
        <p class="body">{{ data.summary.overview }}</p>
        <p class="muted">生成于 {{ formatTime(data.summary.generated_at) }}</p>
      </template>
      <p v-if="data.summary.changed_after !== null" class="muted">之后目录里的记忆又改过，摘要可能不是最新的。</p>
      <DevOnly label="生成记录"><pre>{{ JSON.stringify(data.runs.map(run => ({ ...run, status: runLabel[run.status] || run.status })), null, 2) }}</pre></DevOnly>
    </template>
    <template v-else-if="data">
      <p v-if="data.freshness?.pending_child_changes" class="muted">目录里的记忆改过，概览可能不是最新的。</p>
      <p class="body">{{ data.content }}</p>
      <DevOnly label="新鲜度"><pre>{{ JSON.stringify(data.freshness, null, 2) }}</pre></DevOnly>
    </template>
  </section>
</template>

<style scoped>
.summary{display:grid;gap:8px}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.summary p{margin:0}
.body{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
