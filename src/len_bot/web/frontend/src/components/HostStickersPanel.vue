<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, queryString } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const props = defineProps({ scene:{ type:String, required:true }, timezone:{ type:String, required:true } })
const emit = defineEmits(['dirty', 'busy'])
const state = ref(null), candidates = ref(null), candidate = ref(null), draft = ref(null)
const calls = ref(null), call = ref(null), imageErrors = ref({})
const reviewFilter = ref('pending'), statusFilter = ref('all')
const stateLoading = ref(false), candidatesLoading = ref(false), candidateLoading = ref(false)
const callsLoading = ref(false), callLoading = ref(false), saving = ref(false), deleting = ref(false), retrying = ref(false), running = ref(false)
const stateError = ref(''), candidatesError = ref(''), candidateError = ref(''), callsError = ref(''), callError = ref('')
const saveError = ref(''), actionError = ref(''), saveNotice = ref(''), actionNotice = ref('')
const beginState = useRequestGuard(() => props.scene)
const beginCandidates = useRequestGuard(() => `${props.scene}\u0000${reviewFilter.value}\u0000${statusFilter.value}`)
const beginCandidate = useRequestGuard(() => props.scene)
const beginCalls = useRequestGuard(() => props.scene)
const beginCall = useRequestGuard(() => props.scene)
const beginMutation = useRequestGuard(() => props.scene)
const beginAction = useRequestGuard(() => props.scene)
const busy = computed(() => saving.value || deleting.value || retrying.value || running.value)
function labels(value) { return value.split(/\r?\n/).map(row => row.trim()).filter(Boolean) }
function body() {
  return { description:draft.value.description === '' ? null : draft.value.description,
    text:draft.value.text === (candidate.value.text ?? '') ? candidate.value.text : draft.value.text,
    emotions:draft.value.emotions === candidate.value.emotions.join('\n') ? candidate.value.emotions : labels(draft.value.emotions),
    tags:draft.value.tags === candidate.value.tags.join('\n') ? candidate.value.tags : labels(draft.value.tags),
    review:draft.value.review }
}
const dirty = computed(() => candidate.value !== null && draft.value !== null &&
  JSON.stringify(body()) !== JSON.stringify({ description:candidate.value.description,
    text:candidate.value.text, emotions:candidate.value.emotions, tags:candidate.value.tags,
    review:candidate.value.review }))
watch(dirty, value => emit('dirty', value), { immediate:true })
watch(busy, value => emit('busy', value), { immediate:true })
onBeforeUnmount(() => { emit('dirty', false); emit('busy', false) })
function endpoint(suffix = '') { return `/api/host/scenes/${encodeURIComponent(props.scene)}/learning/stickers${suffix}` }
function imageUrl(id) { return endpoint(`/candidates/${id}/image`) }
function localTime(value) {
  if (value === null || value === undefined) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:props.timezone, timeZoneName:'short', hour12:false })
}
function processingLabel(value) {
  return { queued:'排队待处理', running:'处理中', complete:'标注完成', failed:'失败', interrupted:'已中断' }[value] || value
}
function reviewLabel(value) { return { pending:'待人工审核', adopted:'人工采用', rejected:'人工拒绝' }[value] || value }
function originalText(message) {
  return message.segments.map(part => part.type==='text' ? part.data.text
    : `[${part.type} ${JSON.stringify(part.data)}]`).join('')
}
function errorMessage(error, verb) {
  return error.status >= 400 && error.status < 500
    ? `${verb}未被接受：${error.message}`
    : `${verb}结果未确认：${error.message} 草稿保留；请手动重读核对，不会自动重试。`
}
function adopt(value) {
  candidate.value = value
  draft.value = { description:value.description ?? '', text:value.text ?? '',
    emotions:value.emotions.join('\n'), tags:value.tags.join('\n'), review:value.review }
}
async function readState() {
  const fresh = beginState(); stateLoading.value = true
  try { const value = await api(endpoint()); if (fresh()) { state.value = value; stateError.value = '' } }
  catch (error) { if (fresh()) stateError.value = error.message }
  finally { if (fresh()) stateLoading.value = false }
}
async function readCandidates(more = false, force = false) {
  if ((!force && candidatesLoading.value) ||
      (more && (!candidates.value || candidates.value.items.length >= candidates.value.total))) return
  const offset = more ? candidates.value.items.length : 0, fresh = beginCandidates()
  candidatesLoading.value = true
  try {
    const page = await api(`${endpoint('/candidates')}?${queryString({
      review:reviewFilter.value==='all'?null:reviewFilter.value,
      status:statusFilter.value==='all'?null:statusFilter.value, limit:20, offset,
    })}`)
    if (fresh()) {
      candidates.value = more ? { ...page, items:[...candidates.value.items, ...page.items] } : page
      candidatesError.value = ''
    }
  } catch (error) { if (fresh()) candidatesError.value = error.message }
  finally { if (fresh()) candidatesLoading.value = false }
}
async function openCandidate(id) {
  if (candidate.value?.id === id) return
  if (dirty.value && !window.confirm('放弃当前图像审核草稿并打开另一候选？')) return
  const fresh = beginCandidate(); candidateLoading.value = true
  candidate.value = null; draft.value = null; candidateError.value = ''
  try {
    const value = await api(endpoint(`/candidates/${id}`))
    if (!fresh()) return
    adopt(value); saveError.value = ''; saveNotice.value = ''
  } catch (error) { if (fresh()) candidateError.value = error.message }
  finally { if (fresh()) candidateLoading.value = false }
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
  if (call.value?.id === id) { beginCall(); call.value = null; callLoading.value = false; callError.value = ''; return }
  const fresh = beginCall(); callLoading.value = true; call.value = null; callError.value = ''
  try { const value = await api(endpoint(`/calls/${id}`)); if (fresh()) call.value = value }
  catch (error) { if (fresh()) callError.value = error.message }
  finally { if (fresh()) callLoading.value = false }
}
async function saveCandidate() {
  if (!dirty.value || busy.value || !candidate.value) return
  if (draft.value.review==='adopted' &&
      (candidate.value.status!=='complete' || candidate.value.media_id===null || !draft.value.description.trim())) {
    saveError.value = '采用须已完成标注、保存原图并填写非空描述；模型的 is_sticker 判断不替代人工决定。'
    return
  }
  const fresh = beginMutation(), id = candidate.value.id
  saving.value = true; saveError.value = ''; saveNotice.value = ''
  try {
    const value = await api(endpoint(`/candidates/${id}`), { method:'PUT', body:JSON.stringify(body()) })
    if (!fresh() || candidate.value?.id !== id) return
    adopt({ ...value, source_message:candidate.value.source_message })
    saveNotice.value = '人工审核已保存；不保证 react 会选中，也不代表图片已发送或 QQ 客户端已收到。'
    readCandidates(false, true); readState()
  } catch (error) { if (fresh()) saveError.value = errorMessage(error, '保存群表情候选') }
  finally { if (fresh()) saving.value = false }
}
async function deleteCandidate() {
  if (!candidate.value || busy.value || candidate.value.status==='running' ||
      !window.confirm('删除此候选？原聊天、已存原图、视觉调用与发送历史都会保留。')) return
  const fresh = beginMutation(), id = candidate.value.id
  deleting.value = true; saveError.value = ''; saveNotice.value = ''
  try {
    await api(endpoint(`/candidates/${id}`), { method:'DELETE' })
    if (!fresh() || candidate.value?.id !== id) return
    candidate.value = null; draft.value = null
    saveNotice.value = '候选已删除；原聊天、原图与调用/发送历史未删除。'
    readCandidates(false, true); readState()
  } catch (error) { if (fresh()) saveError.value = errorMessage(error, '删除群表情候选') }
  finally { if (fresh()) deleting.value = false }
}
async function retryCandidate() {
  if (!candidate.value || busy.value || !['failed','interrupted'].includes(candidate.value.status)
      || candidate.value.review==='rejected') return
  const fresh = beginMutation(), id = candidate.value.id
  retrying.value = true; actionError.value = ''; actionNotice.value = ''
  try {
    const result = await api(endpoint(`/candidates/${id}/retry`), { method:'POST' })
    if (!fresh() || candidate.value?.id !== id) return
    adopt({ ...result.candidate, source_message:candidate.value.source_message })
    state.value = { ...state.value, service_state:result.state }
    actionNotice.value = '已请求重做该失败/中断候选；不代表下载、视觉模型或审核已完成。'
    readCandidates(false, true); readState(); readCalls(false, true)
  } catch (error) { if (fresh()) actionError.value = errorMessage(error, '重做群表情候选') }
  finally { if (fresh()) retrying.value = false }
}
async function requestRun() {
  if (!state.value?.enabled || busy.value) return
  const fresh = beginAction(); running.value = true; actionError.value = ''; actionNotice.value = ''
  try {
    const result = await api(endpoint('/run'), { method:'POST' })
    if (!fresh()) return
    state.value = { ...state.value, service_state:result.state }
    actionNotice.value = '已请求检查排队图片；没有待处理项时不会创建视觉调用，尚未证明完成。'
  } catch (error) { if (fresh()) actionError.value = errorMessage(error, '请求检查群表情') }
  finally { if (fresh()) running.value = false }
}
function refresh() {
  if (dirty.value && !window.confirm('放弃当前图像审核草稿并重读此群？')) return
  beginCandidate(); beginCall()
  candidate.value = null; draft.value = null; call.value = null; imageErrors.value = {}
  candidateLoading.value = false; callLoading.value = false; candidateError.value = ''; callError.value = ''
  readState(); readCandidates(false, true); readCalls(false, true)
}
watch([reviewFilter,statusFilter], () => {
  beginCandidates(); candidates.value = null; candidatesLoading.value = false; candidatesError.value = ''
  readCandidates()
})
onMounted(refresh)
</script>

<template>
  <section class="surface stickers-panel" aria-labelledby="stickers-title">
    <header class="section-heading"><div><h2 id="stickers-title">本群表情候选</h2>
      <p class="muted">真实群友入站图片先是候选，不先判作表情；视觉结论只供人工审核。角色表情与本群候选是不同来源。</p></div>
      <v-btn variant="outlined" :loading="stateLoading || candidatesLoading || callsLoading" :disabled="busy" @click="refresh">手动重读群图</v-btn></header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'状态读取失败 · 保留上次快照':'状态读取失败'">{{ stateError }}</v-alert>
    <p v-if="stateLoading && !state" role="status">正在读取群图处理状态…</p>
    <template v-if="state"><p><strong>收集与使用：{{ state.enabled?'当前配置启用':'当前未启用' }}</strong> · 工作器 {{ state.service_state?.running?'正在运行':'未运行' }}。</p>
      <p class="muted">待处理 {{ state.counts.queued }} · 处理中 {{ state.counts.running }} · 标注完成 {{ state.counts.complete }} · 失败 {{ state.counts.failed }} · 中断 {{ state.counts.interrupted }}。关闭后保留候选与原图，但 react 仅用角色素材。</p>
      <details v-if="state.service_state"><summary>查看后台状态原文</summary><pre>{{ JSON.stringify(state.service_state,null,2) }}</pre></details>
      <v-btn color="primary" :loading="running" :disabled="!state.enabled || busy" @click="requestRun">请求检查排队图片</v-btn></template>
    <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
    <p v-if="actionNotice" class="success-note" role="status">{{ actionNotice }}</p>

    <div class="subsection"><div class="section-heading"><h3>来源图片墙</h3><span class="muted">每页最多 20 条 · 只读已保存本地原件</span></div>
      <div class="filters"><v-select v-model="reviewFilter" label="人工决定" :items="[{title:'待审核',value:'pending'},{title:'全部',value:'all'},{title:'已采用',value:'adopted'},{title:'已拒绝',value:'rejected'}]" hide-details="auto" :disabled="busy" />
        <v-select v-model="statusFilter" label="处理状态" :items="[{title:'全部',value:'all'},{title:'排队',value:'queued'},{title:'处理中',value:'running'},{title:'完成',value:'complete'},{title:'失败',value:'failed'},{title:'中断',value:'interrupted'}]" hide-details="auto" :disabled="busy" /></div>
      <v-alert v-if="candidatesError" type="error" variant="tonal" role="alert" :title="candidates?'读取失败 · 保留上次图片墙':'读取失败'">{{ candidatesError }}</v-alert>
      <p v-if="candidatesLoading && !candidates" role="status">正在读取候选图片…</p>
      <p v-if="candidates && !candidates.items.length" class="muted">此筛选下没有已保存候选。</p>
      <ul v-if="candidates?.items.length" class="image-wall"><li v-for="item in candidates.items" :key="item.id" class="image-card">
        <a v-if="item.media_id!==null" :href="imageUrl(item.id)" target="_blank" rel="noopener" :aria-label="`打开已保存原图：${item.description || '未标注群图'}`" class="image-frame">
          <span v-if="imageErrors[item.id]" class="image-error" role="status">原图当前不可读取；打开链接查看接口错误。</span>
          <img v-else :src="imageUrl(item.id)" :alt="item.description || '尚未标注的群友图片'" loading="lazy" :width="item.meta?.width" :height="item.meta?.height" @error="imageErrors[item.id]=true" />
        </a><div v-else class="image-frame image-missing">尚无已保存原图；请查看下载/处理错误原文。</div>
        <div class="image-facts"><strong>{{ reviewLabel(item.review) }} · {{ processingLabel(item.status) }}</strong>
          <p class="muted">来源消息 {{ item.source_message_seq }} 的第 {{ item.image_index }} 张 · {{ localTime(item.created) }}</p>
          <p class="original-text">{{ item.description ?? '尚无描述' }}</p>
          <p v-if="item.meta" class="muted">{{ item.meta.mime_type }} · {{ item.meta.width }}×{{ item.meta.height }}<span v-if="item.meta.animated"> · 动图原件</span> · {{ item.meta.bytes }} 字节</p>
          <p v-if="item.error" class="original-text error-text">{{ item.error }}</p>
          <v-btn variant="text" :disabled="busy || candidateLoading" @click="openCandidate(item.id)">查看来源与审核</v-btn></div>
      </li></ul>
      <v-btn v-if="candidates && candidates.items.length < candidates.total" variant="outlined" :loading="candidatesLoading" :disabled="candidatesLoading || busy" @click="readCandidates(true)">读取更多候选</v-btn>
      <p v-if="candidateLoading" role="status">正在读取来源原话与候选详情…</p>
      <v-alert v-if="candidateError" type="error" variant="tonal" role="alert">{{ candidateError }}</v-alert>
      <form v-if="candidate && draft" class="candidate-editor" @submit.prevent="saveCandidate"><h4>人工审核 · 来源消息 {{ candidate.source_message_seq }} 的第 {{ candidate.image_index }} 张</h4>
        <p>处理：{{ processingLabel(candidate.status) }}；决定：{{ reviewLabel(candidate.review) }}；模型判断：{{ candidate.is_sticker===null?'尚无':candidate.is_sticker?'可能是表情':'可能不是表情' }}。</p>
        <p class="muted">模型判断不能替代人工决定；即使模型判 false，仍可在已完成标注且有原件时明确采用。</p>
        <p v-if="candidate.error" class="original-text error-text">{{ candidate.error }}</p>
        <p v-if="candidate.meta?.animated" class="muted">视觉模型只分析动图首帧；保留和发送的是原动图字节。</p>
        <p v-if="candidate.status!=='complete'" class="muted">标注未完成时只能保留待审核或明确拒绝；描述、图中文字和标签须等处理完成后再编辑，避免被后续视觉结果覆盖。</p>
        <v-textarea v-model="draft.description" label="人工描述（采用时必填）" rows="3" auto-grow hide-details="auto" :disabled="busy || candidate.status!=='complete'" />
        <v-textarea v-model="draft.text" label="图中文字（可留空）" rows="2" auto-grow hide-details="auto" :disabled="busy || candidate.status!=='complete'" />
        <div class="tag-grid"><v-textarea v-model="draft.emotions" label="情绪标签（每行一个）" rows="3" auto-grow hide-details="auto" :disabled="busy || candidate.status!=='complete'" />
          <v-textarea v-model="draft.tags" label="其他标签（每行一个）" rows="3" auto-grow hide-details="auto" :disabled="busy || candidate.status!=='complete'" /></div>
        <p class="muted">标签按换行拆分：忽略空行和首尾空白，保留标签内部文字与空格；编辑草稿不会自动保存。</p>
        <v-select v-model="draft.review" label="人工决定" :items="candidate.status==='complete'?[{title:'待审核',value:'pending'},{title:'采用',value:'adopted'},{title:'拒绝',value:'rejected'}]:[{title:'待审核',value:'pending'},{title:'拒绝',value:'rejected'}]" hide-details="auto" :disabled="busy" />
        <p v-if="dirty" class="dirty-note" role="status">图像审核草稿尚未保存。</p>
        <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
        <p v-if="saveNotice" class="success-note" role="status">{{ saveNotice }}</p>
        <div class="actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || busy">保存人工审核</v-btn>
          <v-btn v-if="['failed','interrupted'].includes(candidate.status) && candidate.review!=='rejected'" variant="outlined" :loading="retrying" :disabled="busy" @click="retryCandidate">明确重做这张图</v-btn>
          <v-btn variant="outlined" color="error" :loading="deleting" :disabled="busy || candidate.status==='running'" @click="deleteCandidate">删除候选</v-btn></div>
        <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
        <p v-if="actionNotice" class="success-note" role="status">{{ actionNotice }}</p>
        <h4>实际来源消息</h4><template v-if="candidate.source_message?.body"><p class="muted">{{ localTime(candidate.source_message.body.time) }}</p>
          <p class="original-text">{{ originalText(candidate.source_message.body) }}</p>
          <details><summary>查看原生消息段</summary><pre>{{ JSON.stringify(candidate.source_message.body,null,2) }}</pre></details></template>
        <p v-else class="muted">来源消息 {{ candidate.source_message_seq }} 当前不可读取，不以相似原话替代。</p>
      </form>
      <p v-if="saveNotice && !candidate" class="success-note" role="status">{{ saveNotice }}</p>
    </div>

    <div class="subsection"><div class="section-heading"><h3>实际下载与视觉调用</h3><span class="muted">列表不载大请求/响应；未开始模型不计视觉已执行</span></div>
      <v-alert v-if="callsError" type="error" variant="tonal" role="alert" :title="calls?'读取失败 · 保留上次列表':'读取失败'">{{ callsError }}</v-alert>
      <p v-if="callsLoading && !calls" role="status">正在读取调用…</p>
      <p v-if="calls && !calls.items.length" class="muted">此场景没有已保存视觉调用。</p>
      <ul v-if="calls?.items.length" class="records"><li v-for="item in calls.items" :key="item.id" class="record">
        <div class="record-head"><strong>{{ processingLabel(item.status) }}</strong><span>{{ localTime(item.started) }}</span></div>
        <p class="muted">来源消息 {{ item.source_message_seq }} 的第 {{ item.image_index }} 张 · {{ item.model_started===null?'未开始视觉模型':`视觉模型开始 ${localTime(item.model_started)}` }} · 费用 {{ item.model_started===null?'无模型调用':item.cost===null?'未知':JSON.stringify(item.cost) }}</p>
        <p v-if="item.error" class="original-text error-text">{{ item.error }}</p>
        <v-btn variant="text" :disabled="callLoading" @click="openCall(item.id)">{{ call?.id===item.id?'收起详情':'查看实际请求与响应' }}</v-btn>
        <div v-if="call?.id===item.id" class="call-detail"><p>模型用量：{{ call.model_started===null?'未开始':call.usage===null?'未知':JSON.stringify(call.usage) }}；结束 {{ localTime(call.ended) }}</p>
          <details><summary>来源消息原话与原生段</summary><pre>{{ JSON.stringify(call.source_message,null,2) }}</pre></details>
          <details><summary>请求原文</summary><pre>{{ JSON.stringify(call.request,null,2) }}</pre></details>
          <details><summary>原始响应</summary><pre>{{ JSON.stringify(call.response,null,2) }}</pre></details></div>
      </li></ul>
      <v-alert v-if="callError" type="error" variant="tonal" role="alert">{{ callError }}</v-alert>
      <p v-if="callLoading" role="status">正在读取原始请求与响应…</p>
      <v-btn v-if="calls && calls.items.length < calls.total" variant="outlined" :loading="callsLoading" :disabled="callsLoading" @click="readCalls(true)">读取更多调用</v-btn>
    </div>
  </section>
</template>

<style scoped>
.stickers-panel{min-width:0;overflow-wrap:anywhere}.section-heading,.record-head,.actions{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}
.section-heading h2{font-size:18px;margin:0 0 8px}.section-heading h3{font-size:16px;margin:0}.section-heading>div{min-width:0;flex:1 1 450px}
.subsection{border-top:1px solid var(--line);padding-top:18px;margin-top:22px}.filters,.tag-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:12px 0}
.image-wall,.records{list-style:none;padding:0;margin:16px 0;display:grid;gap:14px}.image-wall{grid-template-columns:repeat(auto-fit,minmax(min(100%,235px),1fr))}
.image-card,.record,.candidate-editor{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0;overflow-wrap:anywhere}.image-frame{display:grid;place-items:center;width:100%;height:185px;border-radius:8px;background:var(--list-heading-bg);overflow:hidden}
.image-frame img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}.image-frame:focus-visible{outline:3px solid var(--primary);outline-offset:2px}.image-missing,.image-error{text-align:center;padding:12px;color:var(--muted);font-size:13px}
.image-facts p{margin:7px 0;font-size:13px}.image-facts strong{display:block;margin:11px 0 6px}.error-text{color:var(--error-text)}.original-text{white-space:pre-wrap;overflow-wrap:anywhere}
.candidate-editor{display:grid;gap:12px;margin-top:18px}.candidate-editor h4{font-size:15px;margin:0}.actions{justify-content:flex-start;margin:12px 0}.call-detail{background:var(--list-heading-bg);border-radius:8px;padding:12px;margin-top:10px}
.stickers-panel pre{font:inherit;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto}.stickers-panel summary{cursor:pointer;min-height:44px}
.dirty-note,.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}.stickers-panel :deep(.v-btn){min-height:44px}.stickers-panel :deep(.v-alert),.stickers-panel .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.image-card,.candidate-editor{padding:10px}}
</style>
