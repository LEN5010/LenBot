<script setup>
import { onMounted, ref } from 'vue'
import { api, queryString } from '../api.js'
const props = defineProps({ scene: { type:String, required:true }, path: { type:String, required:true } })
const data = ref(null), result = ref(null), error = ref(''), busy = ref(false)
async function read() {
  data.value = await api('/api/host/memory/native-overview?' + queryString({scene:props.scene,path:props.path}))
}
async function operate(refresh = false) {
  if (busy.value) return
  busy.value = true; error.value = ''; data.value = null
  if (refresh) result.value = null
  try {
    if (refresh) result.value = await api('/api/host/memory/native-overview', {method:'POST',
      body:JSON.stringify({scene:props.scene,path:props.path})})
    await read()
  } catch (caught) {
    error.value = refresh ? `刷新或重读未完成：${caught.message} 请求超时不代表远端已停止；结果未确认时不会自动重试。` : caught.message
  } finally { busy.value = false }
}
onMounted(() => operate())
</script>

<template>
  <section class="native-overview">
    <h3>原生目录概览</h3>
    <p>仅当前目录，不是含全部成员资料的群画像；新鲜度计数只覆盖直接子项。刷新将递归重建本目录的概览与向量，调用远端自身配置的服务，不刷新上级目录。</p>
    <div class="actions"><v-btn :disabled="busy" variant="text" @click="operate()">重读原生概览</v-btn>
      <v-btn :disabled="busy" variant="outlined" @click="operate(true)">刷新原生概览与向量</v-btn></div>
    <p v-if="busy" role="status">正在等待远端结果…</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <div v-if="result" role="status">
      <p>{{ result.complete ? '远端处理完成，未报告失败或不支持项。' : '远端处理结束，但存在失败或不支持项，不算完整成功。' }}</p>
      <p>扫描 {{ result.result.scanned_records }} · 重建 {{ result.result.rebuilt_records }} · 失败 {{ result.result.failed_records }} · 不支持 {{ result.result.unsupported_records }}</p>
      <pre v-if="result.result.warnings.length">{{ result.result.warnings.join('\n') }}</pre>
    </div>
    <template v-if="data">
      <p v-if="data.freshness === null">远端没有提供新鲜度，状态未知。</p>
      <template v-else>
        <p>直接子项 {{ data.freshness.total_entries }} · 已采样 {{ data.freshness.sampled_entries }} · 未采样 {{ data.freshness.unsampled_entries }} · 待处理变更 {{ data.freshness.pending_child_changes }} · 缺摘要 {{ data.freshness.missing_summary_entries ?? '未报告' }}</p>
        <p v-if="data.freshness.pending_child_changes">概览已落后于原文。</p>
      </template>
      <pre>{{ data.content }}</pre>
    </template>
  </section>
</template>
<style scoped>
.native-overview{border-top:1px solid var(--line);margin-top:14px;padding-top:12px;display:grid;gap:10px;overflow-wrap:anywhere}
.actions{display:flex;gap:10px;flex-wrap:wrap}pre{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
