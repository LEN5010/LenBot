<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { api, queryString, fmtTime } from '../api.js'
import ResourceViewer from './ResourceViewer.vue'

const props = defineProps({ eventId: { type: String, required: true }, sceneId: { type: String, required: true } })
const record = ref(null), loading = ref(false), error = ref('')
let sequence = 0
watch(() => [props.eventId, props.sceneId], () => {
  ++sequence;
  record.value = null;
  error.value = '';
  loading.value = false
}, {flush:'sync'})
onBeforeUnmount(() => {
  ++sequence
})

async function load() {
  const own = ++sequence, eventId=props.eventId, sceneId=props.sceneId
  loading.value = true;
  error.value = '';
  record.value = null
  try {
    const value = await api(`/api/cockpit/events/${encodeURIComponent(eventId)}/diagnostics?${queryString({ scene_id: sceneId })}`)
    if (own !== sequence) return
    if(value.root?.event_id!==eventId||value.root?.scene_id!==sceneId)throw new Error('诊断材料不属于本次事件与场景，未开放下载。')
    record.value = value
  } catch (failure) {
    if (own === sequence) error.value = failure.message
  }
  finally {
    if (own === sequence) loading.value = false
  }
}

function download() {
  const url = URL.createObjectURL(new Blob([JSON.stringify(record.value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url;
  link.download = 'lenbot-event-diagnostics.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
</script>

<template>
  <section class="event-diagnostics" aria-label="只读诊断材料">
    <h3>只读诊断材料</h3>
    <p>读取此事件的受限关联，导出身份、状态、用量和缺档说明；不包含正文、工具参数、错误原文或请求快照。仍含业务编号，分享前请先预览核对。</p>
    <v-btn variant="outlined" size="small" :loading="loading" @click="load">读取并预览诊断材料</v-btn>
    <v-alert v-if="error" type="error" variant="tonal" class="mt-3">读取失败：{{ error }}。本次没有可下载材料。</v-alert>
    <template v-if="record">
      <p>读取区间：{{ fmtTime(record.read_started_at) }} — {{ fmtTime(record.read_finished_at) }}。下载的是本次预览，不会重新读取或执行。</p>
      <p v-if="Object.values(record.truncated).some(Boolean)">关联已截断；具体类别和上限见预览。该文件不是完整历史，也不是单条消息独占消耗。</p>
      <details>
        <summary>预览导出 JSON</summary>
        <ResourceViewer title="诊断材料（无正文）" :content="record" />
      </details>
      <v-btn variant="text" size="small" @click="download">下载本次诊断 JSON</v-btn>
    </template>
  </section>
</template>

<style scoped>
.event-diagnostics{border-top:1px solid var(--line);padding:16px 0;margin-top:16px;min-width:0}
.event-diagnostics h3{font-size:14px;margin:0 0 8px}
.event-diagnostics p{font-size:12px;line-height:1.7;color:var(--muted);overflow-wrap:anywhere}
.event-diagnostics summary{font-size:12px;cursor:pointer}
.event-diagnostics details{margin:12px 0}
</style>
