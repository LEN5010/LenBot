<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtTime } from '../api.js'
import { developerDetails } from '../composables/useDeveloperMode.js'
const snapshot = ref(null), error = ref(''), busy = ref(false)
async function read() {
  if (busy.value) return
  busy.value = true
  try { snapshot.value = await api('/api/host/logs'); error.value = '' }
  catch (err) { error.value = err.message }
  finally { busy.value = false }
}
const labels = {runtime:'运行状态', receipt:'消息收件', turn:'轮次结束', platform_error:'平台错误', platform_event:'平台事件'}
onMounted(read)
</script>
<template>
  <section class="surface system-logs"><div class="heading"><h1>宿主运行日志</h1><v-btn variant="outlined" :loading="busy" @click="read">读取最新日志</v-btn></div>
    <p class="muted">仅当前进程最近 500 条宿主终端事件，重启清空；不是全部历史或第三方服务日志。模型调用和工作任务的完整过程仍在各自时间线。</p>
    <v-alert v-if="error" type="error" variant="tonal">读取失败，保留上次结果：{{ error }}</v-alert>
    <p v-if="snapshot && !snapshot.items.length" class="muted">本次进程尚无已记录的宿主终端事件。</p>
    <ol v-if="snapshot"><li v-for="(item,index) in snapshot.items" :key="index"><strong>{{ labels[item.record.type] || item.record.type }}</strong> · {{ fmtTime(item.time) }}
      <p v-if="item.record.scene">{{ item.record.scene }}</p>
      <p v-if="item.record.status">{{ item.record.status }}</p>
      <pre v-if="item.record.error || item.record.reason">{{ item.record.error || item.record.reason }}</pre>
      <details v-if="developerDetails"><summary>原始结构</summary><pre>{{ JSON.stringify(item.record,null,2) }}</pre></details>
    </li></ol>
  </section>
</template>
<style scoped>
.system-logs{max-width:1100px;margin:auto;overflow-wrap:anywhere}.heading{display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between}h1{margin:0}ol{list-style:none;padding:0}li{padding:16px 0;border-top:1px solid var(--line)}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{min-height:44px;cursor:pointer}p{margin:6px 0}
</style>
