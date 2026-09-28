<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime } from '../api.js'
import { developerDetails } from '../composables/useDeveloperMode.js'
const files=ref(null), filter=ref('all')
const visible=computed(()=>snapshot.value?.items.filter(item=>filter.value==='all'||Boolean(item.record.error))||[])
const snapshot = ref(null), error = ref(''), busy = ref(false)
async function read() {
  if (busy.value) return
  busy.value = true
  try { const [window, stored]=await Promise.all([api('/api/host/logs'),api('/api/host/log-files')]); snapshot.value=window;files.value=stored; error.value = '' }
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
    <v-select v-model="filter" :items="[{title:'全部',value:'all'},{title:'错误',value:'errors'}]" label="本次窗口筛选" />
    <h2>持久日志</h2><p v-if="files && !files.enabled">未开启文件日志。可在系统设置中配置，重启后生效。</p><ul v-if="files"><li v-for="file in files.items" :key="file.name"><a :href="`/api/host/log-files/${encodeURIComponent(file.name)}`">{{file.name}} · {{file.bytes}} 字节 · 下载脱敏包</a></li></ul>
    <ol v-if="snapshot"><li v-for="(item,index) in visible" :key="index"><strong>{{ labels[item.record.type] || item.record.type }}</strong> · {{ fmtTime(item.time) }}
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
