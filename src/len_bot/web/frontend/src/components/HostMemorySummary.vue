<script setup>
import { onMounted, ref } from 'vue'
import { api, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type:String, required:true }, scope: { type:String, required:true },
  path: { type:String, required:true }, timezone: { type:String, default:null } })
const data = ref(null), loading = ref(false), running = ref(false), error = ref(''), notice = ref('')
const begin = useRequestGuard()
const STATUS = { running:'进行中', complete:'已完成', failed:'失败', interrupted:'已中断' }

function time(value) {
  if (value === null || value === undefined) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:props.timezone || undefined, hour12:false })
}
function mutationError(caught) {
  return caught.status >= 400 && caught.status < 500 ? `生成未完成：${caught.message}`
    : `生成结果未确认：${caught.message} 可重读本目录摘要核对；不会自动重试。`
}
async function read() {
  const fresh = begin()
  loading.value = true; error.value = ''
  try {
    const value = await api('/api/host/memory/summary' + queryString({ scene:props.scene, path:props.path, scope:props.scope }))
    if (fresh()) data.value = value
  } catch (caught) { if (fresh()) error.value = caught.message }
  finally { if (fresh()) loading.value = false }
}
async function generate() {
  if (running.value) return
  running.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api('/api/host/memory/summary', { method:'POST',
      body:JSON.stringify({ scene:props.scene, path:props.path, scope:props.scope }) })
    notice.value = result.skipped ? result.skipped : '本目录摘要已生成并写入；上级目录的摘要不会随之自动更新。'
    await read()
  } catch (caught) { error.value = mutationError(caught); await read() }
  finally { running.value = false }
}
onMounted(read)
</script>

<template>
  <div class="summary-box">
    <div class="section-heading"><h3>本目录摘要</h3>
      <div class="actions"><v-btn variant="text" :loading="loading" :disabled="running" @click="read">重读</v-btn>
        <v-btn v-if="data?.enabled" variant="outlined" :loading="running" :disabled="loading" @click="generate">重新生成本目录摘要</v-btn></div></div>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <p v-if="notice" class="success-note" role="status">{{ notice }}</p>
    <p v-if="loading && !data" class="muted" role="status">正在读取摘要…</p>
    <template v-if="data">
      <p v-if="!data.enabled" class="muted">当前运行配置未开启目录摘要；下面只显示目录里已有的摘要文件。</p>
      <p v-if="data.summary.abstract===null" class="muted">这个目录还没有摘要{{ path==='' && scope==='scene' ? '（分区根目录的概览就是本群画像）' : '' }}。</p>
      <template v-else>
        <p><strong>一句话摘要：</strong>{{ data.summary.abstract }}</p>
        <details><summary>查看概览（生成于 {{ time(data.summary.generated_at) }}）</summary><p class="original-text">{{ data.summary.overview }}</p></details>
      </template>
      <v-alert v-if="data.summary.changed_after!==null" type="info" variant="tonal">
        {{ data.summary.abstract===null ? '目录内已有记忆修改' : '摘要生成后目录内又有修改' }}（最近一次 {{ time(data.summary.changed_after) }}）；摘要可能落后于正文。
      </v-alert>
      <details v-if="data.runs.length"><summary>最近 {{ data.runs.length }} 次生成记录</summary>
        <ul class="runs"><li v-for="run in data.runs" :key="run.id">#{{ run.id }} · {{ STATUS[run.status] || run.status }} · {{ time(run.started) }}
          <span v-if="run.cost"> · 估算费用 {{ run.cost.amount }} {{ run.cost.currency }}</span>
          <span v-if="run.error" class="original-text"> · {{ run.error }}</span></li></ul></details>
    </template>
  </div>
</template>

<style scoped>
.summary-box{border-top:1px solid var(--line);margin-top:14px;padding-top:12px;display:grid;gap:8px;overflow-wrap:anywhere}
.section-heading,.actions{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.summary-box h3{font-size:15px;margin:0}.runs{margin:6px 0;padding-left:18px}
.original-text{white-space:pre-wrap}
.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
</style>
