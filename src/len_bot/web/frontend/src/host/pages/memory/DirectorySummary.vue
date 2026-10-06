<script setup>
import { computed } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import DevOnly from '../../ui/DevOnly.vue'

const props = defineProps({
  scene: { type: String, required: true }, scope: { type: String, required: true },
  path: { type: String, required: true }, state: { type: Object, required: true },
})
const summary = useResource(() => api('/api/host/memory/summary?' + queryString({
  scene: props.scene, path: props.path, scope: props.scope })))
const generate = useAction()
const data = computed(() => summary.data.value)
const runLabel = { running: '进行中', complete: '完成', failed: '失败', interrupted: '中断' }

async function regenerate() {
  const result = await generate.run(() => api('/api/host/memory/summary', {
    method: 'POST', body: JSON.stringify({ scene: props.scene, path: props.path, scope: props.scope }) }))
  if (!result) return
  if (result.skipped) notify(result.skipped)
  else if (result.complete === false) notify('更新完了，但有部分内容没处理成功')
  else notify('已更新')
  summary.reload()
}
</script>

<template>
  <Panel v-if="!(data && !data.enabled && data.summary.abstract === null)" :title="path === '' && scope === 'scene' ? '本群画像' : '这个目录的摘要'"
    :description="data?.enabled ? '重新生成会调用记忆整理模型，会消耗 token。' : ''">
    <template v-if="data?.enabled" #actions><v-btn size="small" variant="outlined" :loading="generate.busy.value" @click="regenerate">重新生成</v-btn></template>
    <ErrorNote v-if="generate.error.value" title="没有生成成功" :error="generate.error.value" />
    <ResourceState :resource="summary" error-title="读取摘要失败">
      <p v-if="data.summary.abstract === null" class="muted">还没有摘要。</p>
      <template v-else>
        <p><strong>{{ data.summary.abstract }}</strong></p>
        <p class="body">{{ data.summary.overview }}</p>
        <p class="muted small">生成于 {{ formatTime(data.summary.generated_at) }}<template v-if="data.summary.changed_after !== null">，之后目录里的记忆又改过</template></p>
      </template>
      <DevOnly label="生成记录" :json="data.runs.map(run => ({ ...run, status: runLabel[run.status] || run.status }))" />
    </ResourceState>
  </Panel>
</template>

<style scoped>
p{margin:0}
.body{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
