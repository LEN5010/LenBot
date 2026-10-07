<script setup>
import { ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import ObjectList from '../../ui/ObjectList.vue'
import CodeBlock from '../../ui/CodeBlock.vue'
import LoadMore from '../../ui/LoadMore.vue'

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
  <Panel title="向量调用记录">
    <template #actions>
      <v-btn-toggle v-model="scope" mandatory><v-btn value="scene">本群</v-btn><v-btn value="public">公共</v-btn></v-btn-toggle>
      <v-btn size="small" variant="outlined" :loading="page.loading.value" @click="page.reload()">读取</v-btn>
    </template>
    <ErrorNote v-if="page.error.value" title="读取向量调用失败" :error="page.error.value" />
    <ErrorNote v-if="reading.error.value" title="读取详情失败" :error="reading.error.value" />
    <p v-if="page.data.value && !rows.length" class="muted">没有记录</p>
    <ObjectList divided>
      <li v-for="call in rows" :key="call.id" class="call">
        <div class="inline"><strong>#{{ call.id }} {{ purpose[call.purpose] || call.purpose }}</strong>
          <span class="muted small">{{ formatTime(call.started) }} · {{ call.tokens ? `${call.tokens.input} token` : '没有报告 token' }}{{ call.error ? ' · 出错' : '' }}</span>
          <v-btn v-if="!opened[call.id]" size="small" variant="text" @click="detail(call.id)">详情</v-btn></div>
        <CodeBlock :text="JSON.stringify(opened[call.id] || { usage: call.usage, response: call.response, error: call.error }, null, 2)" />
      </li>
    </ObjectList>
    <LoadMore v-if="page.data.value?.next_offset != null" :loading="page.loading.value" @more="page.reload(true)" />
  </Panel>
</template>

<style scoped>
.call{padding:var(--sp-3) 0;display:grid;gap:var(--sp-2)}
</style>
