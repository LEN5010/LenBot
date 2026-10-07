<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ provider: { type: Object, required: true }, binding: { type: Object, default: null }, canProbe: { type: Boolean, default: true },
  models: { type: Array, default: () => [] } })
const emit = defineEmits(['models'])
const action = useAction(), result = ref(null)
const model = ref(''), kind = ref('text')
const modelIds = computed(() => props.models.map(item => item.id))
watch(() => JSON.stringify([props.provider, props.binding]), () => { result.value = null; action.error.value = null })
function candidate() {
  const row = props.provider
  return { alias: row.saved ? row.alias : null, provider: { api: row.api, base_url: row.base_url, api_key: row.api_key || null, proxy: row.proxy || null } }
}
async function list() {
  const snapshot = JSON.stringify(candidate())
  result.value = null
  const value = await action.run(() => api('/api/host/models/list', { method: 'POST', body: JSON.stringify(candidate()) }))
  if (value && snapshot === JSON.stringify(candidate())) {
    emit('models', value.models)
    result.value = { text: `已读取 ${value.models.length} 个模型` }
  }
}
async function probe() {
  const binding = props.binding || { provider: props.provider.alias || 'probe', model: model.value, context_window_tokens: 8192, max_output_tokens: 1024, temperature: null }
  const snapshot = JSON.stringify([candidate(), binding, kind.value])
  result.value = null
  const value = await action.run(() => api('/api/host/models/probe', { method: 'POST', body: JSON.stringify({ ...candidate(), binding, kind: kind.value }) }))
  if (value && snapshot === JSON.stringify([candidate(), props.binding || binding, kind.value])) result.value = value
}
</script>

<template>
  <div class="stack">
    <div v-if="!binding" class="probe-row">
      <v-combobox v-if="canProbe" v-model="model" :items="modelIds" label="测试模型" />
      <v-btn variant="outlined" :loading="action.busy.value" @click="list">读取模型列表</v-btn>
    </div>
    <template v-if="canProbe && (!binding || binding.model)">
      <v-select v-model="kind" label="测试内容" :items="[{ title: '文本（调用 1 次）', value: 'text' }, { title: '工具调用（调用 2 次）', value: 'tools' }]" />
      <v-btn variant="outlined" :disabled="!binding && !model" :loading="action.busy.value" @click="probe">测试模型</v-btn>
    </template>
    <ErrorNote v-if="action.error.value" :error="action.error.value" title="请求失败" />
    <div v-if="result" role="status" class="stack">
      <p style="white-space: pre-wrap">{{ result.text }}</p>
      <p v-if="result.scope" class="muted small">{{ result.scope }}</p>
      <details v-if="result.usage"><summary>服务商报告的用量</summary><pre>{{ JSON.stringify(result.usage, null, 2) }}</pre></details>
    </div>
  </div>
</template>

<style scoped>
.probe-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--sp-3);align-items:start}
.probe-row .v-btn{margin-top:var(--sp-2)}
@media(max-width:600px){.probe-row{grid-template-columns:1fr}}
</style>
