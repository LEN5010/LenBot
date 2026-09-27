<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type: String, default: '' } })
const snapshot = ref(null), loading = ref(false), acting = ref('')
const readError = ref(''), actionError = ref(''), actionResult = ref(null)
const beginRead = useRequestGuard(), beginAction = useRequestGuard(() => props.scene)
const current = computed(() => snapshot.value?.scenes.find(item => item.scene === props.scene) || null)
const latest = computed(() => current.value?.latest || null)
const statusLabel = value => ({ queued: '已排队', running: '处理中', submitted: '已提交原生任务，未完成',
  complete: '已完成', failed: '失败', interrupted: '已中断' })[value] || value
const backendLabel = value => value === 'local' ? '本地' : value === 'openviking' ? 'OpenViking' : value
const canRun = computed(() => snapshot.value?.enabled && current.value && !current.value.worker_error
  && (latest.value === null || latest.value.status === 'complete'))
const canRetry = computed(() => snapshot.value?.enabled && current.value && !current.value.worker_error
  && latest.value?.backend === 'local' && ['failed', 'interrupted'].includes(latest.value.status))
const canRefresh = computed(() => snapshot.value?.enabled && current.value && !current.value.worker_error
  && latest.value?.backend === 'openviking' && ['submitted', 'failed'].includes(latest.value.status)
  && Boolean(latest.value.details?.task_id))
function when(value) { return value === null ? '未结束' : new Date(value * 1000).toISOString() }
async function read() {
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/memory/ingest')
    if (!fresh()) return
    snapshot.value = value; readError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function request(action) {
  if (!props.scene || acting.value || !({ run: canRun.value, retry: canRetry.value, refresh: canRefresh.value })[action]) return
  const target = props.scene, fresh = beginAction()
  acting.value = action; actionError.value = ''; actionResult.value = null
  try {
    const result = await api(`/api/host/memory/ingest/${encodeURIComponent(target)}/${action}`, { method: 'POST' })
    if (!fresh()) return
    actionResult.value = result
  } catch (error) {
    if (fresh()) actionError.value = error.status >= 400 && error.status < 500
      ? `请求未被接受：${error.message}`
      : `请求结果未确认：${error.message} 不会自动重试；请手动重读作业状态。`
  } finally { if (fresh()) acting.value = '' }
}
watch(() => props.scene, () => { actionError.value = ''; actionResult.value = null; acting.value = '' })
onMounted(read)
</script>

<template>
  <section class="surface memory-ingest" aria-labelledby="ingest-title">
    <header class="section-heading"><div><p class="eyebrow">运行事实 · 手动读取</p><h2 id="ingest-title">记忆后台抽取</h2></div>
      <v-btn variant="outlined" :loading="loading" @click="read">重读作业状态</v-btn></header>
    <p class="muted">首次开启只从当时已保存消息末尾之后接收新输入，不自动回填旧历史。手动“检查一批”若无新输入可能不会创建作业；页面不自动轮询，也不会把提交远端任务称作完成。</p>
    <p v-if="snapshot?.enabled" class="muted">定向遗忘只作用于目标文件及其可访问记忆历史版本，并使明确选择的原消息停止后台抽取；未选的相同内容、未来再次讲述、聊天记录和备份不因此清除。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次作业结果':'读取抽取状态失败'">{{ readError }}</v-alert>
    <p v-if="loading && !snapshot" role="status" class="muted">正在读取抽取配置与作业…</p>
    <template v-if="snapshot">
      <p v-if="!snapshot.enabled" class="muted">当前进程未启用后台抽取；此处没有空作业。下方保存配置不会热启抽取器。</p>
      <template v-else-if="!props.scene"><p class="muted">请先选择配置场景。</p></template>
      <template v-else-if="!current"><p class="muted">当前选择的场景不在运行抽取列表中。</p></template>
      <template v-else>
        <p class="muted">下一批从原始消息位置 {{ current.after_seq }} 之后读取；首次启用时间：{{ when(current.enabled_at) }}。</p>
        <v-alert v-if="current.worker_error" type="error" variant="tonal" role="alert" title="此场景抽取执行者已停止"><pre>{{ current.worker_error }}</pre></v-alert>
        <div class="actions"><v-btn v-if="canRun" variant="outlined" :loading="acting==='run'" :disabled="Boolean(acting)" @click="request('run')">检查当前小批</v-btn>
          <v-btn v-if="canRetry" variant="outlined" :loading="acting==='retry'" :disabled="Boolean(acting)" @click="request('retry')">重试本地失败范围</v-btn>
          <v-btn v-if="canRefresh" variant="outlined" :loading="acting==='refresh'" :disabled="Boolean(acting)" @click="request('refresh')">刷新原生任务结果</v-btn>
          <span v-if="!canRun && !canRetry && !canRefresh" class="muted">当前作业状态没有可用的手动操作；可重读最新状态与错误原文。</span></div>
        <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
        <div v-if="actionResult" class="response"><strong>本次请求的实际返回</strong>
          <p class="muted">{{ actionResult.requested==='run'?'仅请求检查当前小批，不表示创建了作业。':actionResult.requested==='retry'?'已返回新排队作业，不表示抽取已完成。':'仅请求读取原有原生任务，不重新归档。' }}请手动重读作业状态。</p>
          <details><summary>查看完整返回 JSON</summary><pre>{{ JSON.stringify(actionResult,null,2) }}</pre></details></div>
        <div v-if="latest" class="job"><div class="job-heading"><strong>{{ backendLabel(latest.backend) }} · {{ statusLabel(latest.status) }}</strong>
            <span class="muted">{{ when(latest.started) }}</span></div>
          <p class="muted">本次源消息位置 {{ latest.first_seq }} 至 {{ latest.through_seq }}；{{ latest.ended===null?'尚无终结时间':`终结于 ${when(latest.ended)}` }}。</p>
          <details v-if="latest.error"><summary>查看作业错误原文</summary><pre>{{ latest.error }}</pre></details>
          <details><summary>查看实际请求、写入、工具回执与原生任务状态</summary>
            <p class="muted">只展示已落盘 details 的原始字段；没有记录的字段不补零或判成功。</p>
            <pre>{{ JSON.stringify(latest.details,null,2) }}</pre>
          </details>
        </div>
        <p v-else class="muted">当前没有已创建的抽取作业；这不代表历史消息已被回填或本次检查一定会创建作业。</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.memory-ingest{min-width:0;overflow-wrap:anywhere}.section-heading,.job-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}.section-heading h2{font-size:18px;margin:0 0 12px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:14px 0}.job,.response{border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0}
.memory-ingest pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.memory-ingest summary{cursor:pointer;min-height:44px}.memory-ingest :deep(.v-btn){min-height:44px}.memory-ingest :deep(.v-alert),.memory-ingest .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.job,.response{padding:12px}}
</style>
