<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, queryString, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import HostJargonPanel from '../components/HostJargonPanel.vue'
import HostStickersPanel from '../components/HostStickersPanel.vue'
import HostReplyEffectsPanel from '../components/HostReplyEffectsPanel.vue'

const route = useRoute(), router = useRouter()
const host = ref(null), overview = ref(null), settings = ref(null), settingsDraft = ref(null)
const disabledSettingsDraft = ref(null), disabledEmbeddingDraft = ref(null)
const jargonDirty = ref(false), jargonBusy = ref(false), jargonRefreshKey = ref(0)
const stickersDirty = ref(false), stickersBusy = ref(false), stickersRefreshKey = ref(0)
const replyEffectsBusy = ref(false), replyEffectsRefreshKey = ref(0)
const batches = ref(null), batch = ref(null), expressions = ref(null), expression = ref(null), expressionDraft = ref(null)
const embeddingCalls = ref(null), embeddingCall = ref(null)
const filter = ref('pending')
const loading = ref(false), overviewLoading = ref(false), settingsLoading = ref(false)
const batchLoading = ref(false), batchDetailLoading = ref(false), expressionLoading = ref(false), expressionDetailLoading = ref(false)
const embeddingLoading = ref(false), embeddingDetailLoading = ref(false)
const settingsSaving = ref(false), expressionSaving = ref(false), deleting = ref(false), requesting = ref('')
const readError = ref(''), overviewError = ref(''), settingsError = ref(''), batchError = ref(''), batchDetailError = ref('')
const expressionError = ref(''), expressionDetailError = ref(''), settingsSaveError = ref(''), candidateSaveError = ref(''), actionError = ref('')
const embeddingError = ref(''), embeddingDetailError = ref('')
const settingsNotice = ref(''), candidateNotice = ref(''), actionNotice = ref('')
const exampleDraft = ref(null), exampleSaving = ref(false), exampleError = ref(''), exampleNotice = ref('')
const selectedScene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : '')
const options = computed(() => host.value?.scenes.map(item => ({ title:`${sceneName(item.scene)} · ${item.persona.name}`, value:item.scene })) || [])
const settingsDirty = computed(() => settings.value && JSON.stringify(settingsDraft.value) !== JSON.stringify(settings.value.saved))
const expressionDirty = computed(() => expression.value && expressionDraft.value &&
  (expressionDraft.value.situation !== expression.value.situation || expressionDraft.value.style !== expression.value.style || expressionDraft.value.status !== expression.value.status))
const dirty = computed(() => Boolean(settingsDirty.value || expressionDirty.value || jargonDirty.value || stickersDirty.value))
const busy = computed(() => settingsSaving.value || expressionSaving.value || exampleSaving.value || deleting.value || Boolean(requesting.value) || jargonBusy.value || stickersBusy.value || replyEffectsBusy.value)
useUnsavedChanges(dirty)
onBeforeRouteUpdate(to => {
  if (to.query.scene === route.query.scene) return true
  if (busy.value) return false
  return !dirty.value || window.confirm('有未保存的学习设置或候选草稿。放弃草稿并切换场景？')
})
const beginHost = useRequestGuard()
const beginOverview = useRequestGuard(() => selectedScene.value)
const beginSettings = useRequestGuard(() => selectedScene.value)
const beginBatches = useRequestGuard(() => selectedScene.value)
const beginBatch = useRequestGuard(() => selectedScene.value)
const beginEmbeddings = useRequestGuard(() => selectedScene.value)
const beginEmbedding = useRequestGuard(() => selectedScene.value)
const beginExpressions = useRequestGuard(() => `${selectedScene.value}\u0000${filter.value}`)
const beginExpression = useRequestGuard(() => selectedScene.value)
const beginSettingsSave = useRequestGuard(() => selectedScene.value)
const beginExpressionSave = useRequestGuard(() => selectedScene.value)
const beginAction = useRequestGuard(() => selectedScene.value)
const beginExample = useRequestGuard(() => selectedScene.value)
function endpoint(suffix = '') { return `/api/host/scenes/${encodeURIComponent(selectedScene.value)}/learning${suffix}` }
function copy(value) { return JSON.parse(JSON.stringify(value)) }
function defaults() { return { extract:true, jargon_extract:false, collect_stickers:false, reply_effects:false, min_messages:20, batch_size:50, idle_seconds:300, max_age_seconds:1800, auto_adopt:false, embedding:null } }
function numeric(value) { return value === '' ? '' : Number(value) }
function localTime(value) {
  if (value === null || value === undefined || !host.value?.timezone) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone:host.value.timezone, timeZoneName:'short', hour12:false })
}
function statusLabel(value) { return ({ pending:'待审核', adopted:'已采用', rejected:'已拒绝', running:'运行中', complete:'已完成', failed:'失败', interrupted:'已中断' })[value] || value }
function mutationError(error, verb) {
  return error.status >= 400 && error.status < 500
    ? `${verb}未被接受：${error.message}`
    : `${verb}结果未确认：${error.message} 草稿保留；请手动重读核对，不会自动重试。`
}
function selectScene(value) { if (value && value !== selectedScene.value) router.push({ name:route.name, query:{ scene:value } }) }
function resetScene() {
  overview.value = null; settings.value = null; settingsDraft.value = null
  disabledSettingsDraft.value = null; disabledEmbeddingDraft.value = null
  batches.value = null; batch.value = null; expressions.value = null; expression.value = null; expressionDraft.value = null
  embeddingCalls.value = null; embeddingCall.value = null
  overviewError.value = ''; settingsError.value = ''; batchError.value = ''; batchDetailError.value = ''
  expressionError.value = ''; expressionDetailError.value = ''; settingsSaveError.value = ''; candidateSaveError.value = ''; actionError.value = ''
  embeddingError.value = ''; embeddingDetailError.value = ''
  settingsNotice.value = ''; candidateNotice.value = ''; actionNotice.value = ''
  overviewLoading.value = false; settingsLoading.value = false; batchLoading.value = false
  batchDetailLoading.value = false; expressionLoading.value = false; expressionDetailLoading.value = false
  embeddingLoading.value = false; embeddingDetailLoading.value = false
  filter.value = 'pending'
}
async function readHost() {
  const fresh = beginHost(); loading.value = true
  try {
    const value = await api('/api/host/state')
    if (!fresh()) return
    host.value = value; readError.value = ''
    if (!value.scenes.some(item => item.scene === selectedScene.value) && value.scenes.length) {
      await router.replace({ name:route.name, query:{ scene:value.scenes[0].scene } })
    } else if (selectedScene.value) refreshRecords()
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function readOverview() {
  if (!selectedScene.value) return
  const fresh = beginOverview(); overviewLoading.value = true
  try { const value = await api(endpoint()); if (fresh()) { overview.value = value; overviewError.value = '' } }
  catch (error) { if (fresh()) overviewError.value = error.message }
  finally { if (fresh()) overviewLoading.value = false }
}
async function readSettings(confirmDiscard = true) {
  if (!selectedScene.value || (confirmDiscard && settingsDirty.value && !window.confirm('放弃未保存的学习配置草稿并重读根文件？'))) return
  const fresh = beginSettings(); settingsLoading.value = true
  try {
    const value = await api(`/api/host/settings/scenes/${encodeURIComponent(selectedScene.value)}/learning`)
    if (!fresh()) return
    settings.value = value; settingsDraft.value = copy(value.saved)
    disabledSettingsDraft.value = value.saved === null ? null : copy(value.saved)
    disabledEmbeddingDraft.value = value.saved?.embedding === null || value.saved === null ? null : copy(value.saved.embedding)
    settingsError.value = ''; settingsSaveError.value = ''; settingsNotice.value = ''
  } catch (error) { if (fresh()) settingsError.value = error.message }
  finally { if (fresh()) settingsLoading.value = false }
}
async function readBatches(more = false) {
  if (!selectedScene.value || batchLoading.value || (more && (!batches.value || batches.value.items.length >= batches.value.total))) return
  const offset = more ? batches.value.items.length : 0, fresh = beginBatches()
  batchLoading.value = true
  try {
    const page = await api(`${endpoint('/batches')}?${queryString({ offset, limit:20 })}`)
    if (fresh()) { batches.value = more ? { ...page, items:[...batches.value.items, ...page.items] } : page; batchError.value = '' }
  } catch (error) { if (fresh()) batchError.value = error.message }
  finally { if (fresh()) batchLoading.value = false }
}
async function openBatch(id) {
  if (batch.value?.id === id) { batch.value = null; return }
  const fresh = beginBatch(); batchDetailLoading.value = true; batch.value = null; batchDetailError.value = ''
  try { const value = await api(endpoint(`/batches/${id}`)); if (fresh()) { batch.value = value; batchDetailError.value = '' } }
  catch (error) { if (fresh()) batchDetailError.value = error.message }
  finally { if (fresh()) batchDetailLoading.value = false }
}
async function readEmbeddingCalls(more = false) {
  if (!selectedScene.value || embeddingLoading.value ||
      (more && (!embeddingCalls.value || embeddingCalls.value.items.length >= embeddingCalls.value.total))) return
  const offset = more ? embeddingCalls.value.items.length : 0, fresh = beginEmbeddings()
  embeddingLoading.value = true
  try {
    const page = await api(`${endpoint('/embedding-calls')}?${queryString({ offset, limit:20 })}`)
    if (fresh()) {
      embeddingCalls.value = more ? { ...page, items:[...embeddingCalls.value.items, ...page.items] } : page
      embeddingError.value = ''
    }
  } catch (error) { if (fresh()) embeddingError.value = error.message }
  finally { if (fresh()) embeddingLoading.value = false }
}
async function openEmbeddingCall(id) {
  if (embeddingCall.value?.id === id) { embeddingCall.value = null; return }
  const fresh = beginEmbedding(); embeddingDetailLoading.value = true
  embeddingCall.value = null; embeddingDetailError.value = ''
  try {
    const value = await api(endpoint(`/embedding-calls/${id}`))
    if (fresh()) embeddingCall.value = value
  } catch (error) { if (fresh()) embeddingDetailError.value = error.message }
  finally { if (fresh()) embeddingDetailLoading.value = false }
}
async function readExpressions(more = false) {
  if (!selectedScene.value || expressionLoading.value || (more && (!expressions.value || expressions.value.items.length >= expressions.value.total))) return
  const offset = more ? expressions.value.items.length : 0, fresh = beginExpressions()
  expressionLoading.value = true
  try {
    const page = await api(`${endpoint('/expressions')}?${queryString({ status:filter.value === 'all' ? null : filter.value, offset, limit:20 })}`)
    if (fresh()) { expressions.value = more ? { ...page, items:[...expressions.value.items, ...page.items] } : page; expressionError.value = '' }
  } catch (error) { if (fresh()) expressionError.value = error.message }
  finally { if (fresh()) expressionLoading.value = false }
}
async function openExpression(id) {
  if (expression.value?.id === id) return
  if (expressionDirty.value && !window.confirm('放弃当前候选未保存的修改并打开另一条？')) return
  const fresh = beginExpression(); expressionDetailLoading.value = true
  expression.value = null; expressionDraft.value = null; expressionDetailError.value = ''
  try {
    const value = await api(endpoint(`/expressions/${id}`))
    if (!fresh()) return
    expression.value = value
    expressionDraft.value = { situation:value.situation, style:value.style, status:value.status }
    resetExample(value)
    expressionDetailError.value = ''; candidateSaveError.value = ''; candidateNotice.value = ''
  } catch (error) { if (fresh()) expressionDetailError.value = error.message }
  finally { if (fresh()) expressionDetailLoading.value = false }
}
function toggleSettings(enabled) {
  if (enabled) settingsDraft.value = disabledSettingsDraft.value === null ? defaults() : copy(disabledSettingsDraft.value)
  else { disabledSettingsDraft.value = copy(settingsDraft.value); settingsDraft.value = null }
}
function toggleEmbedding(enabled) {
  if (enabled) settingsDraft.value.embedding = disabledEmbeddingDraft.value === null
    ? { provider:'', model:'', dimensions:null } : copy(disabledEmbeddingDraft.value)
  else { disabledEmbeddingDraft.value = copy(settingsDraft.value.embedding); settingsDraft.value.embedding = null }
}
async function saveSettings() {
  if (!settingsDirty.value || settingsSaving.value || settingsLoading.value) return
  const fresh = beginSettingsSave(), target = selectedScene.value
  settingsSaving.value = true; settingsSaveError.value = ''; settingsNotice.value = ''
  try {
    const value = await api(`/api/host/settings/scenes/${encodeURIComponent(target)}/learning`, {
      method:'PUT', body:JSON.stringify({ learning:settingsDraft.value }),
    })
    if (!fresh()) return
    settings.value = value; settingsDraft.value = copy(value.saved)
    if (value.saved !== null) disabledSettingsDraft.value = copy(value.saved)
    if (value.saved?.embedding != null) disabledEmbeddingDraft.value = copy(value.saved.embedding)
    settingsNotice.value = value.restart_required
      ? '学习配置已保存到根文件；当前运行服务不变，重启后生效。'
      : '学习配置已保存；与当前运行值一致。'
  } catch (error) { if (fresh()) settingsSaveError.value = mutationError(error, '保存配置') }
  finally { if (fresh()) settingsSaving.value = false }
}
function refreshCandidateLists() {
  beginExpressions(); expressionLoading.value = false
  readExpressions(); readOverview()
}
function refreshEmbeddingList() {
  beginEmbeddings(); embeddingLoading.value = false
  readEmbeddingCalls()
}
async function saveExpression() {
  if (!expressionDirty.value || expressionSaving.value || !expression.value) return
  const fresh = beginExpressionSave(), id = expression.value.id
  expressionSaving.value = true; candidateSaveError.value = ''; candidateNotice.value = ''
  try {
    const value = await api(endpoint(`/expressions/${id}`), { method:'PUT', body:JSON.stringify(expressionDraft.value) })
    if (!fresh() || expression.value?.id !== id) return
    expression.value = { ...value, source_messages:expression.value.source_messages }
    expressionDraft.value = { situation:value.situation, style:value.style, status:value.status }
    resetExample(value)
    candidateNotice.value = value.status === 'adopted' && value.indexed && overview.value?.selection_enabled && overview.value.voice_mode === 'voice'
      ? '候选已采用且向量已建；下次 voice 可参与检索，不保证实际引用。'
      : '候选决定已保存；是否可被 voice 选用还取决于运行绑定、场景表达方式及向量状态。'
    refreshCandidateLists()
  } catch (error) { if (fresh()) candidateSaveError.value = mutationError(error, '保存候选') }
  finally {
    if (fresh()) { expressionSaving.value = false; refreshEmbeddingList() }
  }
}
function resetExample(value) {
  exampleDraft.value = { context:value.situation, line:value.style, tags:[] }
  exampleError.value = ''; exampleNotice.value = ''
}
async function saveExample() {
  if (!expression.value || expressionDirty.value || exampleSaving.value) return
  const fresh = beginExample(), id = expression.value.id
  exampleSaving.value = true; exampleError.value = ''; exampleNotice.value = ''
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(selectedScene.value)}/persona-examples`, {
      method:'POST', body:JSON.stringify({ expression_id:id, context:exampleDraft.value.context, line:exampleDraft.value.line,
        tags:exampleDraft.value.tags.map(row => row.value) }),
    })
    if (!fresh() || expression.value?.id !== id) return
    const shared = value.affected_scenes.length > 1 ? `该角色包同时用于 ${value.affected_scenes.map(sceneName).join('、')}。` : ''
    exampleNotice.value = `已追加到角色包 examples.yaml。${shared}${value.restart_required ? '运行中的角色不变，重启宿主后生效。' : ''}是否进入常用样例取决于角色的 example_tags 与前 8 条规则。`
  } catch (error) { if (fresh()) exampleError.value = mutationError(error, '转成角色样例') }
  finally { if (fresh()) exampleSaving.value = false }
}
async function deleteExpression() {
  if (!expression.value || deleting.value || !window.confirm(`删除这条表达候选及其审核状态？原聊天消息不删除。\n${expression.value.situation}\n${expression.value.style}`)) return
  const fresh = beginExpressionSave(), id = expression.value.id
  deleting.value = true; candidateSaveError.value = ''; candidateNotice.value = ''
  try {
    await api(endpoint(`/expressions/${id}`), { method:'DELETE' })
    if (!fresh() || expression.value?.id !== id) return
    expression.value = null; expressionDraft.value = null
    candidateNotice.value = '候选已删除；来源聊天原话未删除。'
    refreshCandidateLists()
  } catch (error) { if (fresh()) candidateSaveError.value = mutationError(error, '删除候选') }
  finally { if (fresh()) deleting.value = false }
}
async function requestLearning(action) {
  if (!overview.value?.enabled || requesting.value) return
  const fresh = beginAction(); requesting.value = action; actionError.value = ''; actionNotice.value = ''
  try {
    const value = await api(endpoint(action === 'retry' ? '/retry' : '/request'), { method:'POST' })
    if (!fresh()) return
    overview.value = { ...overview.value, service_state:value.state }
    actionNotice.value = action === 'retry' ? '已请求重做最近失败或中断批次；尚未证明执行完成。' : '已请求检查当前输入；没有新输入时不会创建批次，尚未证明执行完成。'
  } catch (error) { if (fresh()) actionError.value = mutationError(error, '提交学习请求') }
  finally { if (fresh()) requesting.value = '' }
}
function refreshRecords() { readOverview(); readSettings(false); readBatches(); readEmbeddingCalls(); readExpressions() }
function refreshAll() {
  if (dirty.value && !window.confirm('放弃未保存的学习设置或候选草稿，重新读取当前场景？')) return
  expression.value = null; expressionDraft.value = null; batch.value = null; embeddingCall.value = null
  jargonRefreshKey.value++
  stickersRefreshKey.value++
  replyEffectsRefreshKey.value++
  refreshRecords()
}
watch(selectedScene, () => { resetScene(); if (host.value && selectedScene.value) refreshRecords() })
watch(filter, () => {
  beginExpressions()
  expressions.value = null; expressionLoading.value = false; expressionError.value = ''
  readExpressions()
})
onMounted(readHost)
</script>

<template>
  <div class="page-stack host-learning">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>群聊学习</h1>
      <p class="muted">表达、黑话和群图候选各有真实来源及独立开关；人工采用不保证模型下次引用，也不等于平台已发出图片。</p></div>
      <v-btn variant="outlined" :loading="loading || overviewLoading || settingsLoading || batchLoading || embeddingLoading || expressionLoading" :disabled="busy" @click="refreshAll">手动重读</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert">{{ readError }}</v-alert>
    <section class="surface"><h2>场景</h2><v-select :model-value="selectedScene" :items="options" label="选择群聊场景" hide-details="auto" :disabled="busy || !host" @update:model-value="selectScene" />
      <p v-if="host" class="muted">时间按 {{ host.timezone }} 显示；只读取当前宿主已配置的场景。</p></section>
    <template v-if="selectedScene && host">
      <section class="surface"><div class="section-heading"><h2>抽取服务现场</h2><span class="muted">手动刷新查看最新状态</span></div>
        <v-alert v-if="overviewError" type="error" variant="tonal" role="alert" :title="overview?'读取失败 · 保留上次状态':'读取失败'">{{ overviewError }}</v-alert>
        <p v-if="overviewLoading && !overview" role="status">正在读取学习状态…</p>
        <template v-if="overview"><p><strong>后台提取 {{ overview.enabled?'已配置':'未配置' }}</strong> · 工作器 {{ overview.service_state?.running?'正在运行':'未运行' }} · 已处理至原消息位置 {{ overview.cursor?.after_seq ?? '尚未建立游标' }}</p>
          <p><strong>表达选用 {{ overview.selection_enabled?'已配置':'未配置' }}</strong> · 当前表达方式 {{ overview.voice_mode==='voice'?'voice 表达器':'direct 直出' }}。</p>
          <p class="muted">仅 voice 模式且显式绑定表达向量时，已采用并建好向量的候选才可在下一次 voice 检索；direct 不注入。已采用不等于一定会说出原词。</p>
          <p class="muted">待审核 {{ overview.expression_counts.pending }} · 已采用 {{ overview.expression_counts.adopted }} · 已拒绝 {{ overview.expression_counts.rejected }}。禁用服务不会删除已有候选和批次。</p>
          <p v-if="overview.latest">最近批次：{{ statusLabel(overview.latest.status) }} · {{ localTime(overview.latest.started) }}<span v-if="overview.latest.error"> · 错误原文见下方批次详情</span></p>
          <p v-else class="muted">尚无已保存学习批次。</p>
          <details v-if="overview.service_state"><summary>查看服务状态原文</summary><pre>{{ JSON.stringify(overview.service_state,null,2) }}</pre></details>
          <div class="actions"><v-btn color="primary" :loading="requesting==='run'" :disabled="!overview.enabled || busy" @click="requestLearning('run')">请求检查当前批</v-btn>
            <v-btn variant="outlined" :loading="requesting==='retry'" :disabled="!overview.enabled || busy || !['failed','interrupted'].includes(overview.latest?.status)" @click="requestLearning('retry')">重做最近失败批次</v-btn></div>
          <p class="muted">请求只唤醒当前运行服务；没有新输入不会创建批次，不把排队称为模型已执行。失败与中断才可显式重做。</p></template>
        <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
        <p v-if="actionNotice" class="success-note" role="status">{{ actionNotice }}</p>
      </section>
      <section class="surface"><div class="section-heading"><h2>学习配置 · 保存到根文件</h2><RouterLink :to="{name:'host-models'}">查看 learner 模型绑定</RouterLink></div>
        <v-alert v-if="settingsError" type="error" variant="tonal" role="alert">{{ settingsError }}</v-alert>
        <p v-if="settingsLoading && !settings" role="status">正在读取配置…</p>
        <template v-if="settings"><p>当前运行：{{ settings.running===null?'未启用':'已配置' }}；根文件保存值：{{ settings.saved===null?'未启用':'已配置' }}。</p>
          <v-chip variant="tonal" :color="settings.restart_required?'warning':'info'">{{ settings.restart_required?'保存值待重启':'保存值与运行值一致' }}</v-chip>
          <form @submit.prevent="saveSettings"><v-switch :model-value="settingsDraft!==null" label="保存值中配置此群学习能力" :disabled="settingsSaving" hide-details @update:model-value="toggleSettings" />
            <template v-if="settingsDraft"><v-switch v-model="settingsDraft.extract" label="后台提取新表达候选" :disabled="settingsSaving || settingsLoading" hide-details />
              <v-switch v-model="settingsDraft.jargon_extract" label="后台发现与推断黑话" :disabled="settingsSaving || settingsLoading" hide-details />
              <p class="muted">表达与黑话后台开关相互独立，共用下方批次与时机设置；任一开启须在模型页明确绑定 learner。两者都关仍保留已采用表达和黑话解释，整个学习配置设为 null 才停止引用。</p>
              <v-switch v-model="settingsDraft.collect_stickers" label="收集并使用本群表情" :disabled="settingsSaving || settingsLoading" hide-details />
              <p class="muted">本群图像收集独立于 learner；启用须在模型页显式绑定 vision。关闭后保留原图、候选及人工决定，但 react 仅使用角色自带素材；保存根配置不立即改变当前运行。</p>
              <v-switch v-model="settingsDraft.reply_effects" label="记录并判断群友对 Bot 发言的反应" :disabled="settingsSaving || settingsLoading" hide-details />
              <p class="muted">只记录开启后真实确认发出的表达，观察之后 5 条群友消息或 3 分钟；由 learner 批量判断，须在模型页明确绑定 learner。最长积累秒数同时决定待判断样本最迟多久成批。不自动调整人格或主动行为。</p>
              <div class="form-grid">
              <v-text-field :model-value="settingsDraft.min_messages" type="number" step="1" label="触发所需有效群友文字数" hide-details="auto" :disabled="settingsSaving || settingsLoading" @update:model-value="value=>settingsDraft.min_messages=numeric(value)" />
              <v-text-field :model-value="settingsDraft.batch_size" type="number" step="1" label="每批最多扫描原始消息数（含排除项）" hide-details="auto" :disabled="settingsSaving || settingsLoading" @update:model-value="value=>settingsDraft.batch_size=numeric(value)" />
              <v-text-field :model-value="settingsDraft.idle_seconds" type="number" step="any" label="空闲触发秒数" hide-details="auto" :disabled="settingsSaving || settingsLoading" @update:model-value="value=>settingsDraft.idle_seconds=numeric(value)" />
              <v-text-field :model-value="settingsDraft.max_age_seconds" type="number" step="any" label="最长积累秒数" hide-details="auto" :disabled="settingsSaving || settingsLoading" @update:model-value="value=>settingsDraft.max_age_seconds=numeric(value)" />
            </div><v-switch v-model="settingsDraft.auto_adopt" label="自动标记候选为已采用" hide-details :disabled="settingsSaving || settingsLoading" />
              <p class="muted">自动采用只改变审核状态；未建向量或不在 voice 模式时仍不会被选用。</p>
              <v-switch :model-value="settingsDraft.embedding!==null" label="使用显式表达向量绑定供 voice 选用" :disabled="settingsSaving || settingsLoading" hide-details @update:model-value="toggleEmbedding" />
              <div v-if="settingsDraft.embedding" class="form-grid">
                <v-text-field v-model="settingsDraft.embedding.provider" label="向量提供方（根 models.providers 的名称）" hide-details="auto" :disabled="settingsSaving || settingsLoading" />
                <v-text-field v-model="settingsDraft.embedding.model" label="向量模型精确名称" hide-details="auto" :disabled="settingsSaving || settingsLoading" />
                <v-text-field :model-value="settingsDraft.embedding.dimensions ?? ''" type="number" step="1" label="向量维度（留空不指定）" hide-details="auto" :disabled="settingsSaving || settingsLoading" @update:model-value="value=>settingsDraft.embedding.dimensions=value===''||value===null?null:numeric(value)" />
              </div>
              <p class="muted">提供方与模型须显式填写，不继承 mind、learner 或记忆嵌入。首次启用向量或更换绑定后，已有已采用候选缺向量时须停机执行 <code>uv run python -m len_bot.next.reindex_expressions</code>；保存根配置不热改，也不会自动补建。</p></template>
            <p v-else class="muted">学习配置设为 null 会停止新请求引用已采用黑话解释与本群表情，并关闭后台提取；不删除已有候选、词库、原图或批次，运行状态仍须重启才改变。</p>
            <p v-if="settingsDirty" class="dirty-note" role="status">配置草稿尚未保存。</p>
            <v-alert v-if="settingsSaveError" type="error" variant="tonal" role="alert">{{ settingsSaveError }}</v-alert>
            <p v-if="settingsNotice" class="success-note" role="status">{{ settingsNotice }}</p>
            <v-btn type="submit" color="primary" :loading="settingsSaving" :disabled="!settingsDirty || busy || settingsLoading">保存学习配置</v-btn></form>
        </template>
      </section>
      <HostJargonPanel :key="`${selectedScene}:${jargonRefreshKey}`" :scene="selectedScene" :timezone="host.timezone"
        @dirty="jargonDirty=$event" @busy="jargonBusy=$event" />
      <HostStickersPanel :key="`${selectedScene}:${stickersRefreshKey}`" :scene="selectedScene" :timezone="host.timezone"
        @dirty="stickersDirty=$event" @busy="stickersBusy=$event" />
      <HostReplyEffectsPanel :key="`${selectedScene}:${replyEffectsRefreshKey}`" :scene="selectedScene" :timezone="host.timezone"
        @busy="replyEffectsBusy=$event" />
      <section class="surface"><div class="section-heading"><h2>实际学习批次</h2><span class="muted">列表不预载原始模型请求与响应</span></div>
        <v-alert v-if="batchError" type="error" variant="tonal" role="alert">{{ batchError }}</v-alert>
        <p v-if="batchLoading && !batches" role="status">正在读取批次…</p>
        <p v-if="batches && !batches.items.length" class="muted">此群尚无学习批次。</p>
        <ul v-if="batches?.items.length" class="record-list"><li v-for="item in batches.items" :key="item.id" class="record-card">
          <div class="record-head"><strong>{{ statusLabel(item.status) }}</strong><span>{{ localTime(item.started) }}</span></div>
          <p class="muted">原消息位置 ({{ item.after_seq }}, {{ item.through_seq }}]（不含起点，含终点）；结束 {{ localTime(item.ended) }}；费用 {{ item.model_started===null?'尚未调用模型':item.cost===null?'未知':JSON.stringify(item.cost) }}</p>
          <p v-if="item.error" class="original-text">{{ item.error }}</p>
          <v-btn variant="text" :disabled="batchDetailLoading" @click="openBatch(item.id)">{{ batch?.id===item.id?'收起经过':'查看实际请求与响应' }}</v-btn>
          <div v-if="batch?.id===item.id" class="detail"><p>模型开始：{{ localTime(batch.model_started) }}；用量 {{ batch.usage===null?'未知':JSON.stringify(batch.usage) }}</p>
            <p v-if="batch.request?.snapshot_expired_at" class="muted">输入快照已过保留期；调用结果状态和计量保留，不代表原始请求仍可查看。</p>
          <details><summary>原始请求</summary><pre>{{ JSON.stringify(batch.request,null,2) }}</pre></details>
            <details><summary>原始响应</summary><pre>{{ JSON.stringify(batch.response,null,2) }}</pre></details></div>
        </li></ul>
        <v-alert v-if="batchDetailError" type="error" variant="tonal" role="alert">{{ batchDetailError }}</v-alert>
        <v-btn v-if="batches && batches.items.length < batches.total" variant="outlined" :loading="batchLoading" :disabled="batchLoading" @click="readBatches(true)">读取更多批次</v-btn>
      </section>
      <section class="surface"><div class="section-heading"><h2>表达向量请求</h2><span class="muted">真实请求记录 · 非聊天轮次 · 按需读取输入原文</span></div>
        <v-alert v-if="embeddingError" type="error" variant="tonal" role="alert" :title="embeddingCalls?'读取失败 · 保留上次列表':'读取失败'">{{ embeddingError }}</v-alert>
        <p v-if="embeddingLoading && !embeddingCalls" role="status">正在读取表达向量请求…</p>
        <p v-if="embeddingCalls && !embeddingCalls.items.length" class="muted">此场景没有已保存的表达向量请求。</p>
        <ul v-if="embeddingCalls?.items.length" class="record-list"><li v-for="item in embeddingCalls.items" :key="item.id" class="record-card">
          <div class="record-head"><strong>{{ {query:'voice 检索',index:'候选建向量',reindex:'离线重建'}[item.purpose] || item.purpose }}</strong><span>{{ localTime(item.started) }}</span></div>
          <p class="muted">{{ item.ended===null?'尚未结束':`结束 ${localTime(item.ended)}` }} · {{ item.response===null?'几何结果未知':`${item.response.vector_count} 条向量、${item.response.dimensions} 维` }} · 费用 {{ item.cost===null?'未知':JSON.stringify(item.cost) }}</p>
          <p v-if="item.error" class="original-text">{{ item.error }}</p>
          <v-btn variant="text" :disabled="embeddingDetailLoading" @click="openEmbeddingCall(item.id)">{{ embeddingCall?.id===item.id?'收起详情':'查看实际输入与用量' }}</v-btn>
          <div v-if="embeddingCall?.id===item.id" class="detail"><p>关联聊天轮次：{{ embeddingCall.turn_id ?? '无（候选索引或离线重建）' }}；用量 {{ embeddingCall.usage===null?'未知':JSON.stringify(embeddingCall.usage) }}</p>
            <p v-if="embeddingCall.request?.snapshot_expired_at" class="muted">输入快照已过保留期；调用结果状态和计量保留，不代表原始请求仍可查看。</p>
          <details><summary>实际请求（含输入文字与当时价格）</summary><pre>{{ JSON.stringify(embeddingCall.request,null,2) }}</pre></details>
            <details><summary>几何响应与错误</summary><pre>{{ JSON.stringify({response:embeddingCall.response,error:embeddingCall.error},null,2) }}</pre></details></div>
        </li></ul>
        <v-alert v-if="embeddingDetailError" type="error" variant="tonal" role="alert">{{ embeddingDetailError }}</v-alert>
        <v-btn v-if="embeddingCalls && embeddingCalls.items.length < embeddingCalls.total" variant="outlined" :loading="embeddingLoading" :disabled="embeddingLoading" @click="readEmbeddingCalls(true)">读取更多向量请求</v-btn>
      </section>
      <section class="surface"><div class="section-heading"><h2>表达候选与人工决定</h2><span class="muted">审核状态与可检索状态分别显示</span></div>
        <v-select v-model="filter" label="审核状态" :items="[{title:'待审核',value:'pending'},{title:'全部',value:'all'},{title:'已采用',value:'adopted'},{title:'已拒绝',value:'rejected'}]" hide-details="auto" class="filter" :disabled="busy" />
        <v-alert v-if="expressionError" type="error" variant="tonal" role="alert">{{ expressionError }}</v-alert>
        <p v-if="expressionLoading && !expressions" role="status">正在读取候选…</p>
        <p v-if="expressions && !expressions.items.length" class="muted">此筛选下没有表达候选。</p>
        <ul v-if="expressions?.items.length" class="record-list"><li v-for="item in expressions.items" :key="item.id" class="record-card">
          <div class="record-head"><strong>{{ statusLabel(item.status) }} · {{ item.indexed?'向量已建':'尚无向量' }}</strong><span>{{ localTime(item.updated) }}</span></div>
          <p class="original-text">情境：{{ item.situation }}</p><p class="original-text">说法：{{ item.style }}</p>
          <p class="muted">{{ item.count }} 条真实来源；点击查看原话与编辑。</p>
          <v-btn variant="text" :loading="expressionDetailLoading" :disabled="busy || expressionDetailLoading" @click="openExpression(item.id)">查看与审核</v-btn>
        </li></ul>
        <v-btn v-if="expressions && expressions.items.length < expressions.total" variant="outlined" :loading="expressionLoading" :disabled="expressionLoading" @click="readExpressions(true)">读取更多候选</v-btn>
        <v-alert v-if="expressionDetailError" type="error" variant="tonal" role="alert">{{ expressionDetailError }}</v-alert>
        <form v-if="expression && expressionDraft" class="candidate-editor" @submit.prevent="saveExpression"><h3>当前候选 · 原话与人工决定</h3>
          <p class="muted">当前向量：{{ expression.indexed?'已保存；实际检索仍须运行绑定一致且使用 voice 模式':'尚无；仅采用状态不会自动补建历史向量' }}。</p>
          <v-textarea v-model="expressionDraft.situation" label="情境原文" rows="3" auto-grow hide-details="auto" :disabled="expressionSaving || deleting" />
          <v-textarea v-model="expressionDraft.style" label="说法原文" rows="3" auto-grow hide-details="auto" :disabled="expressionSaving || deleting" />
          <v-select v-model="expressionDraft.status" label="人工决定" :items="[{title:'待审核',value:'pending'},{title:'采用',value:'adopted'},{title:'拒绝',value:'rejected'}]" hide-details="auto" :disabled="expressionSaving || deleting" />
          <p v-if="expressionDirty" class="dirty-note" role="status">当前候选有未保存修改。</p>
          <v-alert v-if="candidateSaveError" type="error" variant="tonal" role="alert">{{ candidateSaveError }}</v-alert>
          <p v-if="candidateNotice" class="success-note" role="status">{{ candidateNotice }}</p>
          <div class="actions"><v-btn type="submit" color="primary" :loading="expressionSaving" :disabled="!expressionDirty || busy">保存候选</v-btn>
            <v-btn variant="outlined" color="error" :loading="deleting" :disabled="busy" @click="deleteExpression">删除候选</v-btn></div>
          <section v-if="expression.status==='adopted' && exampleDraft" class="example-editor" aria-labelledby="example-title">
            <h4 id="example-title">转成角色样例</h4>
            <p class="muted">把这条已采用的表达追加到当前场景角色包的 examples.yaml 末尾，不改动已有样例和注释；可先改写场景和台词。候选本身保持不变。</p>
            <v-textarea v-model="exampleDraft.context" label="样例场景（context）" rows="2" auto-grow hide-details="auto" :disabled="exampleSaving || expressionDirty" />
            <v-textarea v-model="exampleDraft.line" label="样例台词（line）" rows="2" auto-grow hide-details="auto" :disabled="exampleSaving || expressionDirty" />
            <div v-for="(row,index) in exampleDraft.tags" :key="index" class="actions"><v-text-field v-model="row.value" :label="`标签 ${index+1}`" hide-details="auto" :disabled="exampleSaving" /><v-btn variant="outlined" :disabled="exampleSaving" @click="exampleDraft.tags.splice(index,1)">删除</v-btn></div>
            <v-btn variant="text" :disabled="exampleSaving || expressionDirty" @click="exampleDraft.tags.push({value:''})">添加标签</v-btn>
            <p v-if="expressionDirty" class="muted">候选有未保存修改；先保存或放弃再转成样例。</p>
            <v-alert v-if="exampleError" type="error" variant="tonal" role="alert">{{ exampleError }}</v-alert>
            <p v-if="exampleNotice" class="success-note" role="status">{{ exampleNotice }}</p>
            <v-btn variant="outlined" color="primary" :loading="exampleSaving" :disabled="busy || expressionDirty" @click="saveExample">追加到角色样例</v-btn>
          </section>
          <h4>真实来源原话</h4><p class="muted">显示本场景的来源原话；删除候选会保留原聊天。</p>
          <ol class="source-list"><li v-for="source in expression.source_messages" :key="source.record">
            <p v-if="source.available" class="original-text">{{ source.rendered }}</p><p v-else>原记录不可用（位置 {{ source.record }}）。</p>
            <details v-if="source.available"><summary>查看原生消息段</summary><pre>{{ JSON.stringify(source.message,null,2) }}</pre></details>
          </li></ol>
        </form>
      </section>
      <p v-if="candidateNotice && !expression" class="success-note" role="status">{{ candidateNotice }}</p>
    </template>
  </div>
</template>

<style scoped>
.host-learning{max-width:1200px;margin-inline:auto;overflow-wrap:anywhere}
.example-editor{display:grid;gap:10px;border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.page-intro,.section-heading,.record-head,.actions{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 500px}.page-intro h1{margin:0 0 10px}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0}.surface h2{font-size:18px;margin:0 0 12px}.surface h3{font-size:16px}.section-heading{align-items:center}.section-heading>a{overflow-wrap:anywhere}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:14px 0}.filter{max-width:280px}
.actions{justify-content:flex-start;margin:14px 0}.record-list{list-style:none;padding:0;margin:16px 0;display:grid;gap:12px}
.record-card,.candidate-editor,.source-list>li{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}.record-card p{margin:8px 0}
.record-head{font-size:13px}.record-head strong{font-size:15px}.detail{background:var(--list-heading-bg);border-radius:8px;padding:12px;margin-top:12px}
.candidate-editor{margin-top:18px;display:grid;gap:12px}.source-list{display:grid;gap:10px;padding-left:20px}.source-list>li{list-style:decimal}
.original-text{white-space:pre-wrap;overflow-wrap:anywhere}.host-learning pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;font:inherit;font-size:13px}
.host-learning details{margin-top:10px}.host-learning summary{cursor:pointer;min-height:44px}.dirty-note,.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-learning :deep(.v-btn){min-height:44px}.host-learning :deep(.v-alert),.host-learning .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}.filter{max-width:none}}
</style>
