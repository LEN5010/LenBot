<script setup>
import Panel from '../../ui/Panel.vue'
import { notify } from '../../store.js'
import { useAction } from '../../../composables/useResource.js'
import ErrorNote from '../../ui/ErrorNote.vue'

const props = defineProps({ index: { type: Object, required: true }, command: { type: String, required: true },
  container: { type: Boolean, default: false } })
const copy = useAction()
async function copyCommand() {
  await copy.run(async () => { await navigator.clipboard.writeText(props.command); notify('已复制') })
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
    <p v-if="container">先停止 LenBot，在挂载同一实例目录的维护容器中执行，完成后重新启动。</p>
    <p v-else>先停止 LenBot，执行重建命令，完成后重新启动。</p>
    <pre>{{ command }}</pre>
    <v-btn variant="outlined" :loading="copy.busy.value" @click="copyCommand">复制命令</v-btn>
    <ErrorNote v-if="copy.error.value" title="复制命令失败" :error="copy.error.value" />
  </Panel>
</template>

<style scoped>
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--fill);padding:var(--sp-4);border-radius:var(--radius)}
</style>
