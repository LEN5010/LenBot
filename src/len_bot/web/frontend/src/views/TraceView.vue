<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const traces = ref([]), selected = ref(null), filters = ref({ scene_id: '', kind: '' }), error = ref('')
onMounted(load)
async function load() {
  error.value = ''
  try {
    const params = new URLSearchParams({ limit: '100' })
    for (const [key, value] of Object.entries(filters.value)) if (value.trim()) params.set(key, value.trim())
    traces.value = await api('/api/cockpit/traces?' + params.toString())
    if (selected.value) selected.value = traces.value.find(trace => trace.id === selected.value.id) || selected.value
  } catch (e) { error.value = e.message }
}
function run(trace) { return trace.payload?.conversation || trace.payload || {} }
function runs(trace) { return trace.kind.startsWith('agent_job') ? trace.payload?.runs || [] : [run(trace)] }
function steps(trace) { return runs(trace).flatMap(item => item.steps || []) }
function modelCount(trace) { return runs(trace).reduce((count, item) => count + (item.model_calls_used || 0), 0) }
function candidate(trace) {
  return [...steps(trace)].reverse().find(step => step.terminal_candidate)?.terminal_candidate || run(trace).terminal_candidate || null
}
function messages(trace) { return candidate(trace)?.messages || trace.payload?.result?.message_proposals || [] }
function messageText(message) { return (message.segments || []).map(segment => segment.type === 'image' ? `[图片 ${segment.asset_id}]` : segment.text || '').join('') }
function toolCount(trace) { return runs(trace).reduce((count, item) => count + (item.tool_calls_used || 0), 0) }
function kindLabel(kind) { return { conversation: '对话', conversation_error: '对话失败', agent_job: '信息工作', agent_job_error: '工作失败', reflection: '记忆修订' }[kind] || kind }
function errorText(value) { return typeof value === 'string' ? value : JSON.stringify(value) }
function failures(trace) {
  const payload = trace.payload || {}, values = [payload.error, payload.error_type, payload.failure_reason, ...runs(trace).map(item => item.failure_reason)]
  if (payload.gate?.accepted === false) values.push(payload.gate.reason)
  for (const step of steps(trace)) {
    values.push(step.failure_reason, step.error)
    for (const call of step.tool_calls || []) values.push(call.failure_reason, call.error)
  }
  return [...new Set(values.filter(Boolean).map(errorText))]
}
function commitRejected(trace) {
  const payload = trace.payload || {}
  if (payload.gate?.accepted === true) return false
  return payload.gate?.accepted === false || ['SceneCommitConflict', 'CommitConflict'].includes(payload.error_type)
}
function commitLabel(trace) { return trace.payload?.gate?.accepted === true ? '已提交' : commitRejected(trace) ? '已拒绝' : '暂无提交回执' }
function resultLabel(trace) {
  if (commitRejected(trace)) return '提交被拒绝'
  if (failures(trace).length && !trace.payload?.result && trace.payload?.gate?.accepted !== true) return '运行未完成'
  if (trace.kind.startsWith('agent_job')) return { completed: '资料已完成', partial: '部分结果', failed: '工作失败', interrupted: '已中断', cancelled: '已取消' }[trace.payload?.result?.status] || '工作记录'
  if (messages(trace).length) return `${messages(trace).length} 条表达提案`
  if (candidate(trace) || trace.payload?.result) return '选择沉默'
  return '运行记录'
}
function brief(trace) {
  const failure = failures(trace)[0]
  if (failure && (trace.kind.endsWith('_error') || commitRejected(trace))) return failure
  return trace.payload?.result?.decision_reason || candidate(trace)?.note || trace.payload?.result?.summary || failure || '未留下简短说明'
}
function sourceText(trace) { return trace.payload?.burst?.text || trace.payload?.goal || trace.payload?.job_id || trace.ref_id || '运行记录' }
</script>

<template>
  <div class="trace-view">
    <div class="toolbar"><div class="page-title"><h1>运行记录</h1><p class="muted">查看模型步骤、终结提案、工具结果与具体提交错误。</p></div><button @click="load">刷新</button></div>
    <form class="toolbar filter-bar" @submit.prevent="load"><input v-model="filters.scene_id" placeholder="场景，例如 group:123" /><select v-model="filters.kind"><option value="">全部记录</option><option value="conversation">对话</option><option value="conversation_error">对话失败</option><option value="agent_job">信息工作</option><option value="agent_job_error">工作失败</option></select><button>筛选</button></form>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <div class="activity-list"><button v-for="trace in traces" :key="trace.id" class="activity-card" :class="{ selected: selected?.id === trace.id }" @click="selected = trace"><time class="activity-time">{{ fmtTime(trace.created_at) }}</time><div class="activity-main"><div class="activity-title"><span class="tag" :class="failures(trace).length ? 'warn' : 'ok'">{{ kindLabel(trace.kind) }}</span><strong>{{ sourceText(trace) }}</strong></div><p>{{ brief(trace) }}</p></div><div class="activity-decision"><strong>{{ resultLabel(trace) }}</strong><span>{{ modelCount(trace) }} 次模型 · {{ toolCount(trace) }} 次工具执行</span></div></button><div v-if="!traces.length" class="panel muted empty">还没有运行记录</div></div>
    <section v-if="selected" class="panel detail-panel"><div class="panel-header"><div><div class="bento-badge">{{ selected.scene_id }} · {{ fmtTime(selected.created_at) }}</div><h2>{{ resultLabel(selected) }}</h2></div><button @click="selected = null">关闭</button></div>
      <div v-if="failures(selected).length" class="error-box"><strong>具体错误</strong><p v-for="failure in failures(selected)" :key="failure">{{ failure }}</p></div>
      <div class="detail-grid"><div><h3>输入与结果</h3><p class="detail-copy">{{ sourceText(selected) }}</p><p class="muted">{{ brief(selected) }}</p><div class="kv"><span class="k">读取截点</span><span class="v">{{ run(selected).read_cutoff ?? '—' }}</span></div><div class="kv"><span class="k">提交结果</span><span class="v">{{ commitLabel(selected) }}</span></div></div><div><h3>{{ commitRejected(selected) ? '表达提案（未提交）' : '表达提案' }}</h3><article v-for="(message, index) in messages(selected)" :key="index" class="proposed-message"><span class="tag">第 {{ index + 1 }} 条</span><p>{{ messageText(message) }}</p><span v-if="message.reply_to" class="muted">引用消息 {{ message.reply_to }}</span></article><p v-if="!messages(selected).length" class="muted">没有表达提案</p><p class="muted">提案与实际送达分别记录，送达情况见场景回执。</p></div></div>
      <h3>模型与工具步骤</h3><div class="table-scroll"><table><thead><tr><th>步骤</th><th>模型</th><th>耗时</th><th>工具调用</th><th>结果</th></tr></thead><tbody><tr v-for="(step, index) in steps(selected)" :key="index"><td>{{ index + 1 }}<span v-if="step.forced_final" class="tag">终结</span></td><td>{{ step.provider_id }} / {{ step.model }}</td><td>{{ step.latency_ms ?? '—' }} ms</td><td><div v-for="call in step.tool_calls || []" :key="call.id"><code>{{ call.name }}</code><span class="muted"> {{ call.status || '' }}</span></div></td><td><span class="tag" :class="step.failure_reason ? 'bad' : ''">{{ step.failure_reason || step.finish_reason || '—' }}</span><details><summary>步骤详情</summary><pre>{{ JSON.stringify(step, null, 2) }}</pre></details></td></tr><tr v-if="!steps(selected).length"><td colspan="5" class="muted">没有模型步骤记录</td></tr></tbody></table></div>
      <details v-if="candidate(selected)" class="detail-record"><summary>{{ commitRejected(selected) ? '终结候选（未提交）' : '终结候选' }}</summary><pre>{{ JSON.stringify(candidate(selected), null, 2) }}</pre></details>
      <details v-if="run(selected).media_manifest" class="detail-record"><summary>图片与表情来源</summary><pre>{{ JSON.stringify(run(selected).media_manifest, null, 2) }}</pre></details>
      <details class="detail-record"><summary>完整记录</summary><pre>{{ JSON.stringify(selected.payload, null, 2) }}</pre></details>
    </section>
  </div>
</template>

<style scoped>
.activity-list { display:grid;gap:11px }.activity-card { padding:15px 17px;display:grid;grid-template-columns:130px minmax(0,1fr) 160px;gap:16px;align-items:center;cursor:pointer;text-align:left;white-space:normal;background:rgba(255,255,255,.72);border:1px solid rgba(255,255,255,.9);border-radius:15px;box-shadow:var(--shadow-sm) }.activity-card:hover,.activity-card.selected { border-color:var(--border-accent);background:rgba(255,255,255,.93) }.activity-time { color:var(--muted);font-size:.8rem }.activity-title { display:flex;align-items:center;gap:9px }.activity-title strong { overflow:hidden;color:var(--text);font-size:.91rem;text-overflow:ellipsis;white-space:nowrap }.activity-main p { margin:6px 0 0;color:var(--muted);font-size:.82rem;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden }.activity-decision { text-align:right }.activity-decision strong,.activity-decision span { display:block;font-size:.8rem }.activity-decision span { margin-top:4px;color:var(--muted);font-size:.73rem }.detail-panel { margin-top:20px }.detail-grid { display:grid;grid-template-columns:1fr 1fr;gap:24px }.detail-copy,.proposed-message p { color:var(--text-soft);line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere }.proposed-message { margin:12px 0 }.error-box { background:#fef2f2;color:#991b1b;padding:14px 16px;border-radius:12px;overflow-wrap:anywhere }.error-box p { margin:8px 0 0 }.table-scroll { overflow-x:auto }.table-scroll td { vertical-align:top }.table-scroll td .tag { white-space:normal }.detail-record { margin-top:20px }details { color:var(--muted);font-size:.8rem }summary { cursor:pointer }pre { white-space:pre-wrap;overflow-wrap:anywhere }.empty { padding:24px;text-align:center }@media(max-width:760px){.activity-card,.detail-grid{grid-template-columns:1fr}.activity-decision{text-align:left}}
</style>
