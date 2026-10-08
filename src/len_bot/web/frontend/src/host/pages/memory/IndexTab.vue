<script setup>
import Panel from '../../ui/Panel.vue'
import { notify } from '../../store.js'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'
import { api } from '../../../api.js'

defineProps({ index: { type: Object, required: true }, reindexing: Boolean })
const emit = defineEmits(['rebuilt'])
const rebuild = useAction()
async function rebuildIndex() {
  const result = await rebuild.run(() => api('/api/host/memory/reindex', { method: 'POST' }))
  if (result) { emit('rebuilt'); notify(`已重建 ${result.files} 份记忆的索引`) }
}
</script>

<template>
  <Panel title="记忆索引">
    <p>{{ index.retrieval === 'hybrid' ? '当前使用向量与全文检索。' : '当前使用全文检索。' }}</p>
    <v-table>
      <thead><tr><th>配置</th><th>服务地址</th><th>模型</th><th>维数</th></tr></thead>
      <tbody>
        <tr v-for="[key, title] in [['stored', '已有索引'], ['configured', '当前模型']]" :key="key">
          <td>{{ title }}</td><td>{{ index[key]?.base_url ?? '—' }}</td>
          <td>{{ index[key]?.model ?? '未使用向量模型' }}</td><td>{{ index[key]?.dimensions ?? '模型默认值' }}</td>
        </tr>
      </tbody>
    </v-table>
  </Panel>
  <Panel title="重建索引">
    <v-btn variant="outlined" :loading="rebuild.busy.value || reindexing" @click="rebuildIndex">重建索引</v-btn>
    <ErrorNote v-if="rebuild.error.value" title="重建索引失败" :error="rebuild.error.value" />
  </Panel>
</template>
