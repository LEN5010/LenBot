<script setup>
import { computed, onScopeDispose, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { modelChoices, providerCandidate } from '../../providerModels.js'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ provider: { type: Object, required: true }, binding: { type: Object, default: null },
  canProbe: { type: Boolean, default: true }, embedding: { type: Boolean, default: false },
  models: { type: Array, default: () => [] }, selectionLabel: { type: String, default: '选择测试模型' } })
const emit = defineEmits(['models', 'choose-model'])
const action = useAction(), result = ref(null), catalog = ref([])
const model = ref(''), kind = ref('text')
const listed = computed(() => props.models.length ? props.models : catalog.value)
const choices = computed(() => modelChoices(listed.value))
let generation = 0, disposed = false
onScopeDispose(() => { disposed = true; ++generation })
const candidate = () => providerCandidate(props.provider)
const requestBinding = () => props.binding || { provider: props.provider.alias || 'probe', model: model.value,
  context_window_tokens: 8192, max_output_tokens: 1024, temperature: null }
const snapshot = () => JSON.stringify([candidate(), requestBinding(), props.embedding ? 'embedding' : kind.value])
watch(snapshot, () => { ++generation; result.value = null; action.error.value = null }, { flush: 'sync' })
watch(() => JSON.stringify(candidate()), () => { catalog.value = [] })

async function list() {
  const own = ++generation, target = JSON.stringify(candidate())
  const current = () => !disposed && own === generation && target === JSON.stringify(candidate())
  result.value = null
  const value = await action.run(() => api('/api/host/models/list', { method: 'POST', body: JSON.stringify(candidate()) }), current)
  if (value) { catalog.value = value.models; emit('models', value.models); result.value = { text: `已读取 ${value.models.length} 个模型` } }
}
async function probe() {
  const own = ++generation, target = snapshot()
  const current = () => !disposed && own === generation && target === snapshot()
  result.value = null
  const value = await action.run(() => api('/api/host/models/probe', { method: 'POST',
    body: JSON.stringify({ ...candidate(), binding: requestBinding(), kind: props.embedding ? 'embedding' : kind.value }) }), current)
  if (value) result.value = value
}
function choose(id) {
  if (props.binding || !props.canProbe) emit('choose-model', id)
  else model.value = id
}
</script>

<template>
  <div class="stack">
    <div class="probe-row">
      <v-combobox v-if="!binding && canProbe" v-model="model" :items="choices" :return-object="false" label="测试模型" />
      <v-btn variant="outlined" :loading="action.busy.value" @click="list">读取模型列表</v-btn>
    </div>
    <details v-if="listed.length" class="model-list">
      <summary>查看 {{ listed.length }} 个模型</summary>
      <ul><li v-for="item in listed" :key="item.id">
        <div><strong>{{ item.name || item.id }}</strong><code>{{ item.id }}</code></div>
        <v-btn size="small" variant="text" @click="choose(item.id)">{{ binding ? '使用这个模型' : selectionLabel }}</v-btn>
      </li></ul>
    </details>
    <template v-if="(canProbe || embedding) && (!binding || binding.model)">
      <v-select v-if="!embedding" v-model="kind" label="测试内容" :items="[{ title: '文本（调用 1 次）', value: 'text' }, { title: '工具调用（调用 2 次）', value: 'tools' }]" />
      <v-btn variant="outlined" :disabled="!binding && !model" :loading="action.busy.value" @click="probe">{{ embedding ? '测试向量接口' : '测试模型' }}</v-btn>
    </template>
    <ErrorNote v-if="action.error.value" :error="action.error.value" title="请求失败" />
    <div v-if="result" role="status" class="stack">
      <p v-if="result.model">{{ provider.alias }} · {{ result.model }} · {{ result.kind === 'embedding' ? '向量' : result.kind === 'tools' ? '工具续接' : '文本' }}</p>
      <p style="white-space: pre-wrap">{{ result.text }}</p>
      <details v-if="result.usage"><summary>服务商报告的用量</summary><pre>{{ JSON.stringify(result.usage, null, 2) }}</pre></details>
    </div>
  </div>
</template>

<style scoped>
.probe-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--sp-3);align-items:start}
.probe-row .v-btn{margin-top:var(--sp-2)}
.model-list ul{list-style:none;padding:0;margin:var(--sp-2) 0;max-height:20rem;overflow:auto}
.model-list li{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2);padding:var(--sp-2);border-bottom:1px solid var(--line)}
.model-list li div{display:grid;gap:var(--sp-1);min-width:0;overflow-wrap:anywhere}
@media(max-width:600px){.probe-row{grid-template-columns:1fr}.model-list li{align-items:start;flex-wrap:wrap}}
</style>
