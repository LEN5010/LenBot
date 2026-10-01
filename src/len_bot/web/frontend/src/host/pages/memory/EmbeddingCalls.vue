<script setup>
// Developer mode only: the saved embedding requests behind memory search.
import { ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true } })
const scope = ref('scene'), rows = ref([]), opened = ref({})
const purpose = { index: '写入索引', query: '搜索', reindex: '重建索引' }
const page = useResource(async more => ({ more: more === true, ...(await api('/api/host/memory/embedding-calls?' + queryString({
  scene: props.scene, scope: scope.value, limit: '20',
  ...(more === true ? { offset: String(page.data.value.next_offset), snapshot: String(page.data.value.snapshot) } : {}),
}))) }), { immediate: false })
watch(() => page.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.calls] : value.calls })
watch(scope, () => { rows.value = []; page.reload() })
const reading = useAction()
async function detail(id) {
  const result = await reading.run(() => api(`/api/host/memory/embedding-calls/${id}?` + queryString({ scene: props.scene, scope: scope.value })))
  if (result) opened.value = { ...opened.value, [id]: result }
}
</script>

<template>
  <section class="surface calls">
    <div class="head">
      <h2>向量调用记录</h2>
      <v-btn-toggle v-model="scope" mandatory density="compact"><v-btn value="scene">本群</v-btn><v-btn value="public">公共</v-btn></v-btn-toggle>
      <v-btn size="small" variant="outlined" :loading="page.loading.value" @click="page.reload()">读取</v-btn>
    </div>
    <ErrorNote v-if="page.error.value" title="读取向量调用失败" :error="page.error.value" />
    <ErrorNote v-if="reading.error.value" title="读取详情失败" :error="reading.error.value" />
    <p v-if="page.data.value && !rows.length" class="muted">没有记录</p>
    <ul>
      <li v-for="call in rows" :key="call.id">
        <strong>#{{ call.id }} {{ purpose[call.purpose] || call.purpose }}</strong>
        <span class="muted"> · {{ formatTime(call.started) }} · {{ call.cost ? `${call.cost.amount} ${call.cost.currency}` : '费用未知' }}{{ call.error ? ' · 出错' : '' }}</span>
        <v-btn v-if="!opened[call.id]" size="small" variant="text" @click="detail(call.id)">详情</v-btn>
        <pre>{{ JSON.stringify(opened[call.id] || { usage: call.usage, response: call.response, error: call.error }, null, 2) }}</pre>
      </li>
    </ul>
    <v-btn v-if="page.data.value?.next_offset != null" size="small" variant="text" :loading="page.loading.value" @click="page.reload(true)">显示更多</v-btn>
  </section>
</template>

<style scoped>
.calls{display:grid;gap:8px}
.head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.head h2{margin-right:auto !important}
ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:var(--code-bg);padding:8px;border-radius:8px;margin:4px 0 0}
</style>
