<script setup>
import { ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ provider: { type: Object, required: true }, binding: { type: Object, default: null }, canProbe: { type: Boolean, default: true } })
const emit = defineEmits(['models'])
const action = useAction(), result = ref(null)
const model = ref(''), kind = ref('text')
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
    result.value = { text: `读取到 ${value.models.length} 个模型。可在用途页选择，也可以手动填写；模型列表不代表都支持工具或图片。` }
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
    <v-btn v-if="!binding" variant="outlined" :loading="action.busy.value" @click="list">读取模型列表</v-btn>
    <v-text-field v-if="!binding && canProbe" v-model="model" label="要测试的模型名" hint="可直接手动填写，无需先保存服务商" persistent-hint />
    <template v-if="canProbe && (!binding || binding.model)">
      <v-select v-model="kind" label="测试内容" :items="[{ title: '文本连接 · 1 次调用', value: 'text' }, { title: '工具调用与续接 · 2 次调用', value: 'tools' }]" />
      <p class="muted small">测试会真实调用模型并可能计费，不向聊天平台发送消息。测试新设置，不改变运行中的绑定。</p>
      <v-btn variant="outlined" :disabled="!binding && !model" :loading="action.busy.value" @click="probe">测试模型</v-btn>
    </template>
    <ErrorNote v-if="action.error.value" :error="action.error.value" title="请求失败，可检查地址、凭据和协议后重试；模型名也可手动填写" />
    <div v-if="result" role="status" class="stack">
      <p style="white-space: pre-wrap">{{ result.text }}</p>
      <p v-if="result.scope" class="muted small">{{ result.scope }} · {{ result.calls }} 次真实调用</p>
      <details v-if="result.usage"><summary>本次服务商报告的用量（不纳入聊天额度）</summary><pre>{{ JSON.stringify(result.usage, null, 2) }}</pre></details>
    </div>
  </div>
</template>
