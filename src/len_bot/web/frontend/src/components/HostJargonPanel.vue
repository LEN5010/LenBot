<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene: { type:String, required:true }, timezone: { type:String, required:true } })
const emit = defineEmits(['dirty', 'busy'])
const state = ref(null), terms = ref(null), term = ref(null), draft = ref(null)
const calls = ref(null), call = ref(null), filter = ref('pending')
const stateLoading = ref(false), termsLoading = ref(false), termLoading = ref(false)
const callsLoading = ref(false), callLoading = ref(false), saving = ref(false), deleting = ref(false), requesting = ref('')
const stateError = ref(''), termsError = ref(''), termError = ref(''), callsError = ref(''), callError = ref('')
const saveError = ref(''), actionError = ref(''), saveNotice = ref(''), actionNotice = ref('')
const beginState = useRequestGuard(() => props.scene)
const beginTerms = useRequestGuard(() => `${props.scene}\u0000${filter.value}`)
const beginTerm = useRequestGuard(() => props.scene)
const beginCalls = useRequestGuard(() => props.scene)
const beginCall = useRequestGuard(() => props.scene)
const beginMutation = useRequestGuard(() => props.scene)
const beginAction = useRequestGuard(() => props.scene)
const dirty = computed(() => term.value !== null && draft.value !== null &&
  (draft.value.meaning !== term.value.meaning || draft.value.status !== term.value.status))
const busy = computed(() => saving.value || deleting.value || Boolean(requesting.value))
watch(dirty, value => emit('dirty', value), { immediate:true })
watch(busy, value => emit('busy', value), { immediate:true })
onBeforeUnmount(() => { emit('dirty', false); emit('busy', false) })
function endpoint(suffix = '') { return `/api/host/scenes/${encodeURIComponent(props.scene)}/learning/jargon${suffix}` }
function localTime(value) {
  if (value === null || value === undefined) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:props.timezone, timeZoneName:'short', hour12:false })
}
function statusLabel(value) {
  return { pending:'待审核', adopted:'已采用', rejected:'已拒绝', running:'运行中', complete:'已完成',
    failed:'失败', interrupted:'已中断' }[value] || value
}
function errorMessage(error, verb) {
  return error.status >= 400 && error.status < 500
    ? `${verb}未被接受：${error.message}`
    : `${verb}结果未确认：${error.message} 草稿保留；请手动重读核对，不会自动重试。`
}
function sourceText(body) {
  return body.segments.map(part => part.type === 'text' ? part.data.text
    : `[${part.type} ${JSON.stringify(part.data)}]`).join('')
}
function sourcePerson(body) {
  return body.is_self ? 'Bot' : body.sender.card || body.sender.nickname || `QQ ${body.sender.uid}`
}
async function readState() {
  const fresh = beginState(); stateLoading.value = true
  try { const value = await api(endpoint()); if (fresh()) { state.value = value; stateError.value = '' } }
  catch (error) { if (fresh()) stateError.value = error.message }
  finally { if (fresh()) stateLoading.value = false }
}
async function readTerms(more = false, force = false) {
  if ((!force && termsLoading.value) || (more && (!terms.value || terms.value.items.length >= terms.value.total))) return
  const offset = more ? terms.value.items.length : 0, fresh = beginTerms()
  termsLoading.value = true
  try {
    const page = await api(`${endpoint('/terms')}?${queryString({ status:filter.value==='all'?null:filter.value, limit:20, offset })}`)
    if (fresh()) {
      terms.value = more ? { ...page, items:[...terms.value.items, ...page.items] } : page
      termsError.value = ''
    }
  } catch (error) { if (fresh()) termsError.value = error.message }
  finally { if (fresh()) termsLoading.value = false }
}
async function openTerm(id) {
  if (term.value?.id === id) return
  if (dirty.value && !window.confirm('放弃当前黑话解释草稿并打开另一词？')) return
  const fresh = beginTerm(); termLoading.value = true
  term.value = null; draft.value = null; termError.value = ''
  try {
    const value = await api(endpoint(`/terms/${id}`))
    if (!fresh()) return
    term.value = value
    draft.value = { meaning:value.meaning, status:value.status }
    saveError.value = ''; saveNotice.value = ''
  } catch (error) { if (fresh()) termError.value = error.message }
  finally { if (fresh()) termLoading.value = false }
}
async function readCalls(more = false, force = false) {
  if ((!force && callsLoading.value) || (more && (!calls.value || calls.value.items.length >= calls.value.total))) return
  const offset = more ? calls.value.items.length : 0, fresh = beginCalls()
  callsLoading.value = true
  try {
    const page = await api(`${endpoint('/calls')}?${queryString({ limit:20, offset })}`)
    if (fresh()) { calls.value = more ? { ...page, items:[...calls.value.items, ...page.items] } : page; callsError.value = '' }
  } catch (error) { if (fresh()) callsError.value = error.message }
  finally { if (fresh()) callsLoading.value = false }
}
async function openCall(id) {
  if (call.value?.id === id) {
    beginCall(); call.value = null; callLoading.value = false; callError.value = ''
    return
  }
  const fresh = beginCall(); callLoading.value = true
  call.value = null; callError.value = ''
  try { const value = await api(endpoint(`/calls/${id}`)); if (fresh()) call.value = value }
  catch (error) { if (fresh()) callError.value = error.message }
  finally { if (fresh()) callLoading.value = false }
}
async function saveTerm() {
  if (!dirty.value || busy.value || !term.value) return
  if (draft.value.status === 'adopted' && !draft.value.meaning?.trim()) {
    saveError.value = '采用黑话前须填写非空的人工生效解释。'; return
  }
  const fresh = beginMutation(), id = term.value.id
  saving.value = true; saveError.value = ''; saveNotice.value = ''
  try {
    const value = await api(endpoint(`/terms/${id}`), { method:'PUT', body:JSON.stringify({
      meaning:draft.value.meaning === '' ? null : draft.value.meaning, status:draft.value.status,
    }) })
    if (!fresh() || term.value?.id !== id) return
    term.value = { ...value, source_messages:term.value.source_messages }
    draft.value = { meaning:value.meaning, status:value.status }
    saveNotice.value = '人工解释与采用状态已保存；不会改写模型最新推断，也不保证后续每次都能理解。'
    readTerms(false, true); readState()
  } catch (error) { if (fresh()) saveError.value = errorMessage(error, '保存黑话') }
  finally { if (fresh()) saving.value = false }
}
async function deleteTerm() {
  if (!term.value || busy.value || !window.confirm(`删除此黑话候选？原聊天消息保留，未来发现可能再次提出。\n${term.value.term}`)) return
  const fresh = beginMutation(), id = term.value.id
  deleting.value = true; saveError.value = ''; saveNotice.value = ''
  try {
    await api(endpoint(`/terms/${id}`), { method:'DELETE' })
    if (!fresh() || term.value?.id !== id) return
    term.value = null; draft.value = null
    saveNotice.value = '此词已删除；原聊天消息保留，未来发现可能再次提出。'
    readTerms(false, true); readState()
  } catch (error) { if (fresh()) saveError.value = errorMessage(error, '删除黑话') }
  finally { if (fresh()) deleting.value = false }
}
async function requestWork(action) {
  if (!state.value?.enabled || busy.value) return
  const fresh = beginAction(); requesting.value = action; actionError.value = ''; actionNotice.value = ''
  try {
    const result = await api(endpoint(action==='retry'?'/retry':'/run'), { method:'POST' })
    if (!fresh()) return
    state.value = { ...state.value, service_state:result.state }
    actionNotice.value = action==='retry'
      ? '已请求重做最近失败或中断的单位；不代表已执行完成。'
      : '已请求检查当前批；没有新输入时可能不创建调用，不代表发现或推断完成。'
  } catch (error) { if (fresh()) actionError.value = errorMessage(error, '提交黑话学习请求') }
  finally { if (fresh()) requesting.value = '' }
}
function refresh() {
  if (dirty.value && !window.confirm('放弃当前黑话解释草稿并重读此群？')) return
  beginTerm(); beginCall()
  term.value = null; draft.value = null; call.value = null
  termLoading.value = false; callLoading.value = false
  termError.value = ''; callError.value = ''
  readState(); readTerms(false, true); readCalls(false, true)
}
watch(filter, () => {
  beginTerms(); terms.value = null; termsLoading.value = false; termsError.value = ''
  readTerms()
})
onMounted(refresh)
</script>

<template>
  <section class="surface jargon-panel" aria-labelledby="jargon-title">
    <header class="section-heading"><div><h2 id="jargon-title">本群黑话</h2><p class="muted">按当前场景查真实出现、模型推断和人工生效解释；词本身不原位改名。</p></div>
      <v-btn variant="outlined" :loading="stateLoading || termsLoading || callsLoading" :disabled="busy" @click="refresh">手动重读黑话</v-btn></header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'状态读取失败 · 保留上次快照':'状态读取失败'">{{ stateError }}</v-alert>
    <p v-if="stateLoading && !state" role="status">正在读取黑话状态…</p>
    <template v-if="state"><p><strong>后台发现与推断：{{ state.enabled?'当前配置启用':'当前未启用' }}</strong> · 工作器 {{ state.service_state?.running?'正在运行':'未运行' }}。</p>
      <p><strong>已采用解释引用：{{ state.selection_enabled?'当前配置允许':'当前配置停止' }}</strong>。后台关闭仍可引用已采用解释；整个学习配置为 null 才停止新请求引用，已有词库不会删除。</p>
      <p class="muted">首次启用起点 {{ state.cursor?.start_seq ?? '尚未建立' }}；已成功处理至 {{ state.cursor?.after_seq ?? '尚未建立' }}。关闭后重开保留原位置，不自动补旧历史。</p>
      <details v-if="state.service_state"><summary>查看后台状态原文</summary><pre>{{ JSON.stringify(state.service_state,null,2) }}</pre></details>
      <div class="actions"><v-btn color="primary" :loading="requesting==='run'" :disabled="!state.enabled || busy" @click="requestWork('run')">请求检查当前批</v-btn>
        <v-btn variant="outlined" :loading="requesting==='retry'" :disabled="!state.enabled || busy || !['failed','interrupted'].includes(state.service_state?.latest?.status)" @click="requestWork('retry')">重做最近失败单位</v-btn></div>
      <p class="muted">请求只唤醒后台工作器，不代表已发现词、完成推断或采用。模型的置信度只是主观声明，不是统计正确率。</p></template>
    <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
    <p v-if="actionNotice" class="success-note" role="status">{{ actionNotice }}</p>

    <div class="subsection"><div class="section-heading"><h3>候选词与人工解释</h3><span class="muted">采用须有非空人工解释；复制推断不等于保存</span></div>
      <v-select v-model="filter" label="审核状态" :items="[{title:'待审核',value:'pending'},{title:'全部',value:'all'},{title:'已采用',value:'adopted'},{title:'已拒绝',value:'rejected'}]" hide-details="auto" class="filter" :disabled="busy" />
      <v-alert v-if="termsError" type="error" variant="tonal" role="alert" :title="terms?'读取失败 · 保留上次列表':'读取失败'">{{ termsError }}</v-alert>
      <p v-if="termsLoading && !terms" role="status">正在读取候选词…</p>
      <p v-if="terms && !terms.items.length" class="muted">此筛选下没有已保存的黑话词。</p>
      <ul v-if="terms?.items.length" class="records"><li v-for="item in terms.items" :key="item.id" class="record">
        <div class="record-heading"><strong class="original-text">{{ item.term }}</strong><span>{{ statusLabel(item.status) }} · 记录出现 {{ item.count }} 次</span></div>
        <p class="original-text">人工生效解释：{{ item.meaning ?? '尚无' }}</p>
        <p class="muted original-text">最新模型推断：{{ item.latest_meaning ?? '尚无' }}；模型主观置信度：{{ item.confidence ?? '未提供' }}</p>
        <v-btn variant="text" :disabled="busy || termLoading" @click="openTerm(item.id)">查看原句与审核</v-btn>
      </li></ul>
      <v-btn v-if="terms && terms.items.length < terms.total" variant="outlined" :loading="termsLoading" :disabled="termsLoading || busy" @click="readTerms(true)">读取更多候选词</v-btn>
      <p v-if="termLoading" role="status">正在读取词的来源原句…</p>
      <v-alert v-if="termError" type="error" variant="tonal" role="alert">{{ termError }}</v-alert>
      <form v-if="term && draft" class="term-editor" @submit.prevent="saveTerm"><h4>人工决定：{{ term.term }}</h4>
        <p class="muted">记录出现 {{ term.count }} 次，最近推断时计数 {{ term.last_inference_count ?? '尚无' }}；更新于 {{ localTime(term.updated) }}。</p>
        <p class="original-text">最新模型推断：{{ term.latest_meaning ?? '尚无' }}；主观置信度：{{ term.confidence ?? '未提供' }}。</p>
        <v-btn v-if="term.latest_meaning !== null" variant="outlined" :disabled="busy" @click="draft.meaning=term.latest_meaning">复制最新推断到解释草稿</v-btn>
        <v-textarea :model-value="draft.meaning ?? ''" label="人工生效解释（清空则不设置）" rows="3" auto-grow hide-details="auto" :disabled="busy" @update:model-value="value=>draft.meaning=value===''?null:value" />
        <v-select v-model="draft.status" label="人工决定" :items="[{title:'待审核',value:'pending'},{title:'采用',value:'adopted'},{title:'拒绝',value:'rejected'}]" hide-details="auto" :disabled="busy" />
        <p v-if="dirty" class="dirty-note" role="status">解释或审核决定尚未保存。</p>
        <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
        <p v-if="saveNotice" class="success-note" role="status">{{ saveNotice }}</p>
        <div class="actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || busy">保存人工决定</v-btn>
          <v-btn variant="outlined" color="error" :loading="deleting" :disabled="busy" @click="deleteTerm">删除此词</v-btn></div>
        <h4>最近来源原句</h4><p class="muted">这些是当前场景真实消息，不替换缺失记录，也不把模型推断当作人工解释。</p>
        <ol class="sources"><li v-for="source in term.source_messages" :key="source.seq">
          <template v-if="source.body"><p class="muted">{{ sourcePerson(source.body) }} · {{ localTime(source.body.time) }}</p>
            <p class="original-text">{{ sourceText(source.body) }}</p><details><summary>查看原生消息</summary><pre>{{ JSON.stringify(source.body,null,2) }}</pre></details></template>
          <p v-else>原消息位置 {{ source.seq }} 当前不可用；不猜相似原话。</p>
        </li></ol>
      </form>
      <p v-if="saveNotice && !term" class="success-note" role="status">{{ saveNotice }}</p>
    </div>

    <div class="subsection"><div class="section-heading"><h3>实际发现与推断调用</h3><span class="muted">列表不预载大请求／原响应</span></div>
      <v-alert v-if="callsError" type="error" variant="tonal" role="alert" :title="calls?'读取失败 · 保留上次列表':'读取失败'">{{ callsError }}</v-alert>
      <p v-if="callsLoading && !calls" role="status">正在读取模型调用…</p>
      <p v-if="calls && !calls.items.length" class="muted">此场景没有已保存的黑话调用。</p>
      <ul v-if="calls?.items.length" class="records"><li v-for="item in calls.items" :key="item.id" class="record">
        <div class="record-heading"><strong>{{ item.purpose==='discovery'?'发现候选':'推断含义' }} · {{ statusLabel(item.status) }}</strong><span>{{ localTime(item.started) }}</span></div>
        <p class="muted">{{ item.purpose==='discovery'?`原消息位置 (${item.after_seq}, ${item.through_seq}]`:`词的累计出现数 ${item.inference_count}` }}；模型开始 {{ localTime(item.model_started) }}；费用 {{ item.model_started===null?'尚未调用模型':item.cost===null?'未知':JSON.stringify(item.cost) }}</p>
        <p v-if="item.error" class="original-text">{{ item.error }}</p>
        <v-btn variant="text" :disabled="callLoading" @click="openCall(item.id)">{{ call?.id===item.id?'收起详情':'查看真实请求与响应' }}</v-btn>
        <div v-if="call?.id===item.id" class="call-detail"><p>实际用量：{{ call.usage===null?'未知':JSON.stringify(call.usage) }}；结束 {{ localTime(call.ended) }}</p>
          <p v-if="call.request?.snapshot_expired_at" class="muted">输入快照已过保留期；调用结果状态和计量保留，不代表原始请求仍可查看。</p>
          <details><summary>请求原文</summary><pre>{{ JSON.stringify(call.request,null,2) }}</pre></details>
          <details><summary>原响应</summary><pre>{{ JSON.stringify(call.response,null,2) }}</pre></details></div>
      </li></ul>
      <v-alert v-if="callError" type="error" variant="tonal" role="alert">{{ callError }}</v-alert>
      <p v-if="callLoading" role="status">正在读取本次真实请求与响应…</p>
      <v-btn v-if="calls && calls.items.length < calls.total" variant="outlined" :loading="callsLoading" :disabled="callsLoading" @click="readCalls(true)">读取更多调用</v-btn>
    </div>
  </section>
</template>

<style scoped>
.jargon-panel{min-width:0;overflow-wrap:anywhere}.section-heading,.record-heading,.actions{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.section-heading h2{font-size:18px;margin:0 0 8px}.section-heading h3{font-size:16px;margin:0}.section-heading>div{min-width:0;flex:1 1 450px}.actions{justify-content:flex-start;margin:14px 0}
.subsection{border-top:1px solid var(--line);padding-top:18px;margin-top:22px}.filter{max-width:280px;margin:12px 0}
.records{list-style:none;padding:0;margin:14px 0;display:grid;gap:12px}.record,.term-editor,.sources>li{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0;overflow-wrap:anywhere}
.record-heading strong{font-size:15px}.record-heading span{font-size:13px;color:var(--muted)}.term-editor{display:grid;gap:12px;margin-top:18px}
.sources{padding-left:20px;display:grid;gap:10px}.sources>li{list-style:decimal}.original-text{white-space:pre-wrap;overflow-wrap:anywhere}
.call-detail{background:var(--list-heading-bg);border-radius:8px;padding:12px;margin-top:10px}.jargon-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;font:inherit;font-size:13px}
.jargon-panel summary{cursor:pointer;min-height:44px}.dirty-note,.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.jargon-panel :deep(.v-btn){min-height:44px}.jargon-panel :deep(.v-alert),.jargon-panel .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.filter{max-width:none}.record,.term-editor{padding:12px}}
</style>
