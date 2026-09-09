<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { mdiArrowLeft, mdiPencilOutline, mdiRefresh, mdiStopCircleOutline, mdiPlayOutline } from '@mdi/js'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import ObservationDetails from '../components/ObservationDetails.vue'
import OperationReceipts from '../components/OperationReceipts.vue'
import PluginWorkDetails from '../components/PluginWorkDetails.vue'
import PluginConfigFields from '../components/PluginConfigFields.vue'
import {configValue} from '../lib/pluginConfig.js'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const jobId = computed(() => scalar(route.params.jobId))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const tab = computed(() => ['result', 'progress', 'budget', 'records'].includes(route.query.tab) ? route.query.tab : 'result')
const filters = ref({ scene: '', status: '', execution: '', query: '' })
const rows = ref([]), total = ref(0), pageSize = ref(30), listLoading = ref(false), listError = ref(''), listLoaded = ref(false), listReadAt = ref(null)
const job = ref(null), detailLoading = ref(false), detailError = ref(''), detailMissing = ref(false), detailReadAt = ref(null)
const records = ref(null), recordsLoading = ref(false), recordsError = ref('')
const resource = ref(null), resourceText = ref(''), resourceLoading = ref(false), resourceError = ref('')
const editing = ref(false), draft = ref({ goal: '', constraints: '', parameters:{} }), baseline = ref(null), conflict = ref(false)
const confirmation = ref(null), saving = ref(false), actionError = ref(''), feedback = ref('')
let listRequest = 0, detailRequest = 0, recordsRequest = 0, resourceRequest = 0
const terminal = new Set(['completed', 'cancelled', 'delivery_unknown', 'shadow_observed'])
const editable = computed(() => job.value && !terminal.has(job.value.status))
const dirty = computed(() => editing.value && baseline.value && (draft.value.goal !== baseline.value.goal || draft.value.constraints !== baseline.value.constraints.join('\n') || Object.keys(draft.value.parameters).length>0))
const { confirmLeave } = useUnsavedChanges(dirty)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const deliveryExplanation = computed(() => ({
  result_ready: '当前版本的执行结果已经保存，正在等待组织首次回应。',
  awaiting_delivery: '当前版本的结果已关联表达行动，正在等待真实发送回执。',
  completed: '已记录此工作的交付完成状态，具体送达以关联回执为准。',
  delivery_unknown: '发送结果未知，需要核对原发送回执；已有研究结果仍然保留。',
  failed: job.value?.result ? '工作保留了执行结果，当前发送没有确认送达。' : '当前工作失败，已保存进度与原因可在本页回查。',
  review_required: '执行已中断，进度和已用预算保留；恢复前需核对当前版本和中断原因。',
  shadow_observed: '本次仅保存了 Shadow 表达，没有真实群聊送达回执。',
  cancelled: '工作已停止，已有资料和历史记录保留。',
}[job.value?.status] || '当前还没有保存首次交付结果。'))
const rangeUnit = unit => ({ characters: '字符', records: '记录' }[unit] || unit)
const spanLabel = span => `${span.start}–${span.end} ${rangeUnit(span.coordinate_unit)}`
const rangesLabel = ranges => ranges.map(([start,end])=>`${start}–${end}`).join('、') || '没有记录采用范围'
const jobOperations = computed(() => (records.value?.operation_receipts || []).filter(item => item.kind==='work' && item.target_id===jobId.value))
const jobActions = computed(() => (records.value?.actions || []).filter(action => action.job_id === jobId.value || action.acknowledges_task_id === jobId.value || action.fulfils_task_id === jobId.value || action.operation_receipt?.kind==='work' && action.operation_receipt.target_id===jobId.value || [job.value?.ack_action_id,job.value?.delivery_action_id].includes(action.id)))
const executionOptions = [{ title: '全部执行状态', value: '' }, { title: '尚未开始', value: 'pending' }, { title: '执行中', value: 'running' }, { title: '执行完成', value: 'completed' }, { title: '部分完成', value: 'partial' }, { title: '执行失败', value: 'failed' }, { title: '执行中断', value: 'interrupted' }, { title: '执行取消', value: 'cancelled' }]
const deliveryOptions = [{ title: '全部交付状态', value: '' }, { title: '等待执行', value: 'pending' }, { title: '处理中', value: 'processing' }, { title: '结果待回应', value: 'result_ready' }, { title: '等待送达', value: 'awaiting_delivery' }, { title: '已送达', value: 'completed' }, { title: '中断待核对', value: 'review_required' }, { title: '送达未知', value: 'delivery_unknown' }, { title: '发送失败', value: 'failed' }, { title: '仅观察', value: 'shadow_observed' }, { title: '已取消', value: 'cancelled' }]
function cleanQuery(values) { return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined)) }
function listQuery() { const { tab: ignoredTab, resource: ignoredResource, list_scene: originalScene, ...values } = route.query; if (originalScene !== undefined) values.scene = scalar(originalScene) || undefined; return values }
function openJob(item) { router.push({ name: 'job', params: { jobId: item.id }, query: { ...listQuery(), list_scene: scalar(route.query.scene), scene: item.scene_id, tab: 'result' } }) }
function backToList() { router.push({ name: 'jobs', query: listQuery() }) }
function setTab(value) { router.replace({ name: 'job', params: { jobId: jobId.value }, query: { ...route.query, tab: value } }) }
function applyFilters() { router.push({ name: 'jobs', query: cleanQuery({ scene: filters.value.scene, status: filters.value.status, execution: filters.value.execution, query: (filters.value.query || '').trim(), page: 1 }) }) }
function setPage(value) { router.push({ name: 'jobs', query: { ...route.query, page: value } }) }

async function loadList() {
  const request = ++listRequest
  listLoading.value = true; listError.value = ''
  const params = new URLSearchParams(cleanQuery({ scene_id: scalar(route.query.scene), status: scalar(route.query.status), execution_status: scalar(route.query.execution), query: scalar(route.query.query), page: page.value, page_size: 30 }))
  try {
    const result = await api('/api/cockpit/jobs?' + params)
    if (request !== listRequest || jobId.value) return
    rows.value = result.items; total.value = result.total; pageSize.value = result.page_size
    listLoaded.value = true; listReadAt.value = Date.now() / 1000
  } catch (error) { if (request === listRequest) listError.value = error.message }
  finally { if (request === listRequest) listLoading.value = false }
}
async function loadJob({ reset = false } = {}) {
  const id = jobId.value, request = ++detailRequest
  if (!id) return
  detailLoading.value = true; detailError.value = ''; detailMissing.value = false
  if (reset) { job.value = null; detailReadAt.value = null; records.value = null; resource.value = null; resourceText.value = ''; ++recordsRequest; ++resourceRequest }
  try {
    const scope = scalar(route.query.scene)
    const value = await api(`/api/cockpit/jobs/${encodeURIComponent(id)}` + (scope ? '?scene_id=' + encodeURIComponent(scope) : ''))
    if (request !== detailRequest || id !== jobId.value) return
    job.value = value; detailReadAt.value = Date.now() / 1000
    if (editing.value && baseline.value && value.revision !== baseline.value.revision) conflict.value = true
    if (tab.value === 'records') await loadRecords()
    if (scalar(route.query.resource)) await loadResource(scalar(route.query.resource))
  } catch (error) { if (request === detailRequest) { detailError.value = error.message; detailMissing.value = error.status === 404; if (detailMissing.value) job.value = null } }
  finally { if (request === detailRequest) detailLoading.value = false }
}
async function loadRecords() {
  if (!job.value) return
  const current = job.value, request = ++recordsRequest
  recordsLoading.value = true; recordsError.value = ''
  try {
    const value = await api('/api/cockpit/relations?' + new URLSearchParams({ scene_id: current.scene_id, job_id: current.id }))
    if (request === recordsRequest && current.id === jobId.value) records.value = value
  } catch (error) { if (request === recordsRequest) recordsError.value = error.message }
  finally { if (request === recordsRequest) recordsLoading.value = false }
}
function openResource(id) { router.replace({ name: 'job', params: { jobId: jobId.value }, query: { ...route.query, tab: 'progress', resource: id } }) }
async function loadResource(id, offset = 0) {
  if (!job.value) return
  const current = job.value, request = ++resourceRequest
  resourceLoading.value = true; resourceError.value = ''
  if (!offset) { resource.value = null; resourceText.value = '' }
  if (!current.result_ids.includes(id)) { resourceError.value = '该资料没有关联到当前工作。'; resourceLoading.value = false; return }
  try {
    const value = await api(`/api/cockpit/tool-results/${encodeURIComponent(id)}?` + new URLSearchParams({ scene_id: current.scene_id, offset }))
    if (request !== resourceRequest || current.id !== jobId.value || scalar(route.query.resource) !== id) return
    resource.value = value; resourceText.value = offset ? resourceText.value + value.content : value.content
  } catch (error) { if (request === resourceRequest) resourceError.value = error.message }
  finally { if (request === resourceRequest) resourceLoading.value = false }
}
function startEdit() {
  if (!editable.value || saving.value) return
  baseline.value = { id: job.value.id, revision: job.value.revision, goal: job.value.goal, constraints: [...job.value.constraints] }
  draft.value = { goal: job.value.goal, constraints: job.value.constraints.join('\n'), parameters:{} }
  conflict.value = false; actionError.value = ''; editing.value = true
}
function cancelEdit() { if (confirmLeave()) { editing.value = false; baseline.value = null; conflict.value = false } }
function reviewLatestVersion() {
  if (!job.value || !baseline.value || !editable.value) return
  baseline.value = { ...baseline.value, revision: job.value.revision, goal: job.value.goal, constraints: [...job.value.constraints] }
  conflict.value = false
  feedback.value = `已选择以当前版本 ${job.value.revision} 核对草稿，请确认目标和要求后再保存。`
}
function askAction(operation) {
  if (saving.value || !job.value) return
  const current = job.value
  if (operation === 'revise') {
    if (!editable.value || !draft.value.goal.trim() || !baseline.value || conflict.value) return
    const conditions = [...new Set(draft.value.constraints.split('\n').map(value => value.trim()).filter(Boolean))]
    let parameters=null
    try { if(current.work_revision_schema && Object.keys(draft.value.parameters).length) parameters=configValue(draft.value.parameters,current.work_revision_schema) }
    catch(error) { actionError.value=error.message; return }
    confirmation.value = { operation, id: current.id, scene: current.scene_id, expected_revision: baseline.value.revision, goal: draft.value.goal.trim()===baseline.value.goal?null:draft.value.goal.trim(), parameters, constraints_add: conditions.filter(value => !baseline.value.constraints.includes(value)), constraints_remove: baseline.value.constraints.filter(value => !conditions.includes(value)) }
  } else {
    confirmation.value = { operation, id: current.id, scene: current.scene_id, expected_revision: current.revision, goal: null, constraints_add: [], constraints_remove: [] }
  }
  actionError.value = ''
}
const confirmationTitle = computed(() => ({ revise: '确认修改工作要求', resume: '确认恢复执行', cancel: '确认停止工作' }[confirmation.value?.operation] || '确认操作'))
async function submitAction() {
  if (saving.value || !confirmation.value) return
  const { operation, id, scene, ...body } = confirmation.value
  const scope = scalar(route.query.scene)
  saving.value = true; ++detailRequest; detailLoading.value = false; actionError.value = ''; feedback.value = ''
  try {
    const result = await api(`/api/cockpit/jobs/${encodeURIComponent(id)}/${operation}`, { method: 'POST', body: JSON.stringify(body) })
    if (jobId.value !== id || scalar(route.query.scene) !== scope) return
    job.value = result.job; detailReadAt.value = Date.now() / 1000; confirmation.value = null
    if (operation === 'revise') { editing.value = false; baseline.value = null; conflict.value = false }
    feedback.value = { revise: '要求已保存，工作版本已更新。', resume: '恢复请求已提交，预算和模型绑定保留。', cancel: '工作已停止，已有资料与历史记录保留。' }[operation]
    records.value = null
  } catch (error) {
    if (jobId.value !== id || scalar(route.query.scene) !== scope) return
    actionError.value = error.message
    confirmation.value = null
    if (error.status === 409) { if (operation === 'revise') conflict.value = true; await loadJob() }
  } finally { saving.value = false }
}
function refresh() { if (saving.value) return; return jobId.value ? loadJob() : loadList() }
function onVisible() { if (document.visibilityState === 'visible') refresh() }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => { ++listRequest; ++detailRequest; ++recordsRequest; ++resourceRequest; document.removeEventListener('visibilitychange', onVisible) })
onBeforeRouteUpdate((to, from) => to.params.jobId !== from.params.jobId || to.query.scene !== from.query.scene ? confirmLeave() : true)
watch(() => [route.params.jobId, route.query.scene], () => {
  ++listRequest; ++detailRequest
  editing.value = false; baseline.value = null; conflict.value = false; confirmation.value = null; actionError.value = ''; feedback.value = ''
  if (jobId.value) loadJob({ reset: true })
}, { immediate: true })
watch(() => [route.query.scene, route.query.status, route.query.execution, route.query.query, route.query.page, route.params.jobId], () => {
  filters.value = { scene: scalar(route.query.scene), status: scalar(route.query.status), execution: scalar(route.query.execution), query: scalar(route.query.query) }
  if (!jobId.value) { rows.value = []; total.value = 0; listLoaded.value = false; listReadAt.value = null; loadList() }
}, { immediate: true })
watch(tab, value => { if (value === 'records' && job.value && !records.value) loadRecords() })
watch(() => route.query.resource, value => { if (value && job.value) loadResource(scalar(value)); else { ++resourceRequest; resource.value = null; resourceText.value = '' } })
</script>

<template>
  <section class="jobs-page">
    <PageHeader :title="jobId ? '工作详情' : '信息工作'" description="执行结果与消息送达分别记录；已有资料和版本可以回查。">
      <v-btn v-if="jobId" variant="text" :prepend-icon="mdiArrowLeft" @click="backToList">返回工作列表</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="jobId ? detailLoading : listLoading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <template v-if="!jobId">
      <v-card class="filter-card">
        <v-card-text><v-form class="job-filters" @submit.prevent="applyFilters">
          <ScopeSelect v-model="filters.scene" clearable />
          <v-text-field v-model="filters.query" label="查找工作目标" hide-details clearable />
          <v-select v-model="filters.execution" :items="executionOptions" label="执行状态" hide-details />
          <v-select v-model="filters.status" :items="deliveryOptions" label="交付状态" hide-details />
          <v-btn type="submit" color="primary">筛选</v-btn>
        </v-form></v-card-text>
      </v-card>
      <v-alert v-if="listError" type="error" variant="tonal" title="工作列表读取失败" class="section-gap">{{ listError }}<div v-if="listReadAt">保留上次读取结果：{{ fmtTime(listReadAt) }}</div></v-alert>
      <v-progress-linear v-if="listLoading" indeterminate class="section-gap" aria-label="正在读取工作" />
      <div v-if="listLoaded" class="list-meta"><span>共 {{ total }} 项 · 每页 {{ pageSize }} 项</span><span>读取于 {{ fmtTime(listReadAt) }}</span></div>
      <div class="work-list">
        <v-card v-for="item in rows" :key="item.id" tag="article" class="work-row">
          <div class="work-main"><RouterLink :to="{ name: 'job', params: { jobId: item.id }, query: { ...listQuery(), list_scene: scalar(route.query.scene), scene: item.scene_id, tab: 'result' } }" class="two-lines record-title">{{ item.goal }}</RouterLink><EntityLink type="scene" :id="item.scene_id" :scene-id="item.scene_id" /><span v-if="item.plugin_origin" class="muted-copy">{{ item.plugin_name || item.plugin_origin.plugin_id }} · {{ item.plugin_origin.plugin_version }}</span></div>
          <div class="status-pair"><span><span class="field-label">执行</span><StatusBadge domain="job_execution" :status="item.execution_status" /></span><span><span class="field-label">交付</span><StatusBadge domain="job_delivery" :status="item.status" /></span></div>
          <div class="work-usage"><span>模型 {{ item.model_steps }} · 工具 {{ item.tool_calls }}</span><time>{{ fmtTime(item.updated_at) }}</time></div>
          <v-btn variant="tonal" @click="openJob(item)">查看详情</v-btn>
        </v-card>
      </div>
      <v-card v-if="listLoaded && !rows.length && !listError" class="empty-state"><v-card-text>没有符合筛选条件的工作。</v-card-text></v-card>
      <v-pagination v-if="pages > 1" :model-value="page" :length="pages" :total-visible="5" class="section-gap" @update:model-value="setPage" />
    </template>
    <template v-else>
      <v-alert v-if="detailError" :type="detailMissing ? 'warning' : 'error'" variant="tonal" :title="detailMissing ? '工作不存在或不属于此场景' : '工作详情读取失败'">{{ detailError }}<div v-if="detailReadAt">上次读取：{{ fmtTime(detailReadAt) }}</div></v-alert>
      <v-skeleton-loader v-if="detailLoading && !job" type="article, list-item-three-line" />
      <template v-if="job">
        <v-card class="job-heading">
          <v-card-text><h2 class="full-title">{{ job.goal }}</h2><div class="identity-line"><EntityLink type="job" :id="job.id" :scene-id="job.scene_id" /><EntityLink type="scene" :id="job.scene_id" :scene-id="job.scene_id" /><span>目标版本 {{ job.revision }}</span></div><div class="identity-line"><span>请求者 QQ {{ job.requester_qq_uid || '未记录' }}</span><EntityLink v-if="job.request_source_event_id" type="event" :id="job.request_source_event_id" :scene-id="job.scene_id" label="提出这项工作的原话" /><span v-else class="muted-copy">旧工作未单独保存请求来源</span></div><div class="detail-status"><span>执行 <StatusBadge domain="job_execution" :status="job.execution_status" /></span><span>交付 <StatusBadge domain="job_delivery" :status="job.status" /></span><span class="read-time">读取于 {{ fmtTime(detailReadAt) }}</span></div><div class="action-row"><v-btn :disabled="!editable || job.plugin_issue || saving || editing" :prepend-icon="mdiPencilOutline" variant="outlined" @click="startEdit">修改要求</v-btn><v-btn :disabled="!job.can_resume || saving || editing" :prepend-icon="mdiPlayOutline" variant="outlined" @click="askAction('resume')">{{ job.execution_status==='partial'?'继续未完成部分':'核对后恢复' }}</v-btn><v-btn :disabled="!editable || saving || editing" :prepend-icon="mdiStopCircleOutline" color="error" variant="outlined" @click="askAction('cancel')">停止工作</v-btn></div></v-card-text>
        </v-card>
        <v-alert v-if="feedback" type="success" variant="tonal" class="section-gap" role="status">{{ feedback }}</v-alert>
        <v-alert v-if="actionError" type="error" variant="tonal" class="section-gap" role="alert">{{ actionError }}</v-alert>
        <v-card v-if="editing" class="section-gap edit-card">
          <v-card-title>修改要求 · 基于版本 {{ baseline.revision }}</v-card-title>
          <v-card-text><v-alert v-if="conflict" type="warning" variant="tonal" class="mb-4">工作已变化，草稿仍然保留。当前服务器版本 {{ job.revision }}；请核对当前目标和要求，再决定是否沿用草稿。<ResourceViewer title="服务器当前目标" :content="job.goal" class="mt-3" /><ResourceViewer title="服务器当前要求" :content="job.constraints" class="mt-3" /><v-btn class="mt-3" variant="outlined" :disabled="!editable" @click="reviewLatestVersion">已核对，改为基于当前版本</v-btn></v-alert><v-textarea v-model="draft.goal" label="工作目标" rows="2" auto-grow :disabled="saving" /><v-textarea v-model="draft.constraints" label="要求（每行一项）" rows="4" auto-grow :disabled="saving" /><template v-if="job.work_revision_schema"><h4>修改插件业务参数</h4><p class="muted-copy">只填写需要改变的字段；未填写的字段保留原值。范围和快照由所属插件处理。</p><PluginConfigFields v-model="draft.parameters" :schema="job.work_revision_schema" /></template><div class="action-row"><v-btn color="primary" :disabled="!editable || !dirty || !draft.goal.trim() || conflict || saving" @click="askAction('revise')">保存修改</v-btn><v-btn variant="text" :disabled="saving" @click="cancelEdit">取消编辑</v-btn></div></v-card-text>
        </v-card>
        <v-card class="section-gap">
          <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="setTab"><v-tab value="result">结果</v-tab><v-tab value="progress">进度与资料</v-tab><v-tab value="budget">预算与压缩</v-tab><v-tab value="records">执行记录</v-tab></v-tabs>
          <v-card-text v-if="tab === 'result'" class="detail-body">
            <section v-if="job.resume_from"><h3>本版继续自 v{{ job.resume_from.revision }}</h3><p>原版交付状态：{{ job.resume_from.response_status }}。旧版结果与已用预算保留。</p><EntityLink v-if="job.resume_from.delivery_event_id" type="event" :id="job.resume_from.delivery_event_id" :scene-id="job.scene_id" label="查看原版交付回执" /><ResourceViewer title="原版结果与未完成项" :content="job.resume_from.result" /></section>
            <h3>首次交付</h3><p>{{ deliveryExplanation }}</p><dl class="summary-facts"><dt>创建确认行动</dt><dd><code v-if="job.ack_action_id">{{ job.ack_action_id }}</code><span v-else class="muted-copy">未保存确认行动引用</span></dd><dt>结果交付行动</dt><dd><code v-if="job.delivery_action_id">{{ job.delivery_action_id }}</code><span v-else class="muted-copy">未保存交付行动引用</span></dd><dt>结果送达回执</dt><dd><EntityLink v-if="job.delivery_event_id" type="event" :id="job.delivery_event_id" :scene-id="job.scene_id" label="读取此工作版本的结果发送回执" /><span v-else class="muted-copy">未保存结果回执引用</span></dd></dl>
            <PluginWorkDetails :job="job" />
            <template v-if="job.result"><h3>当前版本执行结果</h3><ResourceViewer title="完整结果" :content="job.result.summary" /><h3 v-if="job.result.unresolved.length">尚未解决</h3><ul v-if="job.result.unresolved.length"><li v-for="(item, index) in job.result.unresolved" :key="index">{{ item }}</li></ul></template><v-alert v-else type="info" variant="tonal">尚无已保存的执行结果。</v-alert>
            <p v-if="job.result?.reason" class="readable-copy">结果或中断原因：{{ job.result.reason }}</p>
            <template v-if="job.result?.evidence_spans?.length"><h3>结论关联的资料范围</h3><ul><li v-for="(span,index) in job.result.evidence_spans" :key="index"><v-btn variant="text" size="small" @click="openResource(span.result_id)">回读资料 {{ span.result_id.slice(0,10) }}</v-btn><span>{{ spanLabel(span) }}（起含止不含）</span></li></ul></template>
            <ResourceViewer v-if="job.result?.work_state && !job.work_state" title="形成此结果时保存的进度" :content="job.result.work_state" />
            <h3>当前要求</h3><ul v-if="job.constraints.length"><li v-for="(item, index) in job.constraints" :key="index">{{ item }}</li></ul><p v-else class="muted-copy">没有附加要求。</p><h3>已保存的来源与修订原话</h3><p class="muted-copy">以下是工作的资料来源集合；请求者与唯一请求原话单独显示在页首。</p><div class="link-list"><EntityLink v-for="id in job.source_event_ids" :key="id" type="event" :id="id" :scene-id="job.scene_id" /></div>
          </v-card-text>
          <v-card-text v-else-if="tab === 'progress'" class="detail-body">
            <template v-if="job.work_state"><div class="status-line"><h3>已保存进度 · 目标版本 {{ job.work_state.goal_revision }}</h3><v-chip v-if="job.work_state.goal_revision !== job.revision" color="warning" variant="tonal">旧目标进度，需重新核对</v-chip></div><h4>计划</h4><ol><li v-for="(item, index) in job.work_state.plan" :key="index">{{ item }}</li></ol><h4>已完成步骤与依据</h4><ul><li v-for="(step, index) in job.work_state.completed_steps" :key="index">{{ step.step }}<div class="action-row"><v-btn v-for="id in step.result_ids" :key="id" size="small" variant="text" @click="openResource(id)">原始资料 · {{ id.slice(0, 10) }}</v-btn></div><div v-for="(span,spanIndex) in step.evidence_spans || []" :key="spanIndex" class="evidence-range"><v-btn variant="text" size="small" @click="openResource(span.result_id)">{{ span.result_id.slice(0,10) }}</v-btn><span>{{ spanLabel(span) }}（起含止不含）</span></div></li></ul><h4>待解决</h4><ul><li v-for="(item, index) in job.work_state.unresolved" :key="index">{{ item }}</li></ul><p>下一步：{{ job.work_state.next_step || '尚未提供' }}</p><template v-if="job.work_state.evidence_spans?.length"><h4>进度依据的具体范围</h4><div v-for="(span,index) in job.work_state.evidence_spans" :key="index" class="evidence-range"><v-btn variant="text" size="small" @click="openResource(span.result_id)">{{ span.result_id.slice(0,10) }}</v-btn><span>{{ spanLabel(span) }}</span></div></template></template><p v-else class="muted-copy">尚无已保存的进度。</p>
            <h3>已取得的原始工具资料</h3><div class="action-row"><v-btn v-for="id in job.result_ids" :key="id" variant="outlined" :aria-label="'读取原始资料 ' + id" @click="openResource(id)">资料 · {{ id.slice(0, 10) }}</v-btn><p v-if="!job.result_ids.length" class="muted-copy">尚未取得资料。</p></div>
            <h4>此工作实际提供给模型的范围</h4><p class="muted-copy">范围保留实际坐标单位，可以包含工作既有版本的阅读；正文保存本身不表示模型已读。</p><article v-for="(units,id) in job.observation_reads || {}" :key="id" class="adopted-range"><v-btn variant="text" size="small" @click="openResource(id)">资料 {{ id.slice(0,10) }}</v-btn><p v-for="(read,unit) in units" :key="unit">{{ rangesLabel(read.ranges) }} / {{ read.total }} {{ rangeUnit(unit) }}（起含止不含）</p></article><p v-if="!Object.keys(job.observation_reads || {}).length" class="muted-copy">尚未保存实际采用范围，不能从资料数量推定完整读取。</p>
            <ResourceViewer v-if="route.query.resource" title="原始工具资料" :content="resourceText" :loading="resourceLoading" :error="resourceError" /><div v-if="resource" class="resource-meta"><ObservationDetails :observation="resource" :scene-id="job.scene_id" /><v-btn v-if="resource.next_offset !== null && resource.next_offset !== undefined" :loading="resourceLoading" :disabled="resourceLoading" variant="outlined" class="mt-4" @click="loadResource(scalar(route.query.resource), resource.next_offset)">继续读取已保存正文</v-btn></div>
            <h3>实际使用的技能版本</h3><div class="link-list"><EntityLink v-for="(version, id) in job.skill_versions" :key="id" type="skill" :id="id" :scene-id="job.scene_id" :version="version" :label="id + ' · v' + version" /></div><p v-if="!Object.keys(job.skill_versions).length" class="muted-copy">本工作尚未读取技能正文。</p>
          </v-card-text>
          <v-card-text v-else-if="tab === 'budget'" class="detail-body">
            <h3>累计用量与当前发布上限</h3><div class="budget-grid"><div><span>模型请求</span><strong>{{ job.model_steps }} / {{ job.budget.max_model_steps }}</strong></div><div><span>工具调用</span><strong>{{ job.tool_calls }} / {{ job.budget.max_tool_calls }}</strong></div><div><span>执行时间</span><strong>{{ job.elapsed_seconds.toFixed(1) }} / {{ job.budget.max_seconds }} 秒</strong></div></div><p class="muted-copy">已用预算包含工作关联维护，暂停和恢复不重置。这里的上限为运行时当前发布值；实际执行段采用的冻结预算见“执行记录”中的 Trace。</p><h3>固定模型绑定</h3><p v-if="job.model_binding" class="breakable">{{ job.model_binding.provider_id }} / {{ job.model_binding.model }} · 推理 {{ job.model_binding.reasoning_effort || '模型默认' }}</p><p v-else class="muted-copy">尚未绑定模型。</p><h3>最后完整检查点</h3><p v-if="job.checkpoint">{{ job.checkpoint.exchange_count }} 组工具交换 · 目标版本 {{ job.checkpoint.goal_revision }} · {{ fmtTime(job.checkpoint.updated_at) }}</p><p v-else class="muted-copy">尚无完整检查点。</p><h3>上下文预算与压缩</h3><p>有效输入 {{ job.budget.effective_input_tokens.toLocaleString() }} token；总窗口 {{ job.budget.context_tokens.toLocaleString() }}，输出预留 {{ job.budget.output_tokens.toLocaleString() }}。</p><p>触发比例 {{ Math.round(job.budget.compression_trigger * 100) }}%，目标比例 {{ Math.round(job.budget.compression_target * 100) }}%。</p><template v-if="job.compression"><p>压缩原状态：{{ job.compression.status }}</p><v-alert v-if="job.compression.error" type="error" variant="tonal">{{ job.compression.error }}</v-alert><v-expansion-panels variant="accordion" class="mt-4"><v-expansion-panel v-for="(segment, index) in job.compression.segments" :key="index" :title="`工具交换 ${segment.start_exchange}—${segment.end_exchange}`"><v-expansion-panel-text><ResourceViewer title="区间摘要" :content="segment.summary" /><ul v-if="segment.unresolved"><li v-for="(item, itemIndex) in segment.unresolved" :key="itemIndex">{{ item }}</li></ul><div class="action-row"><v-btn v-for="id in segment.result_ids" :key="id" variant="text" @click="openResource(id)">回读资料 · {{ id.slice(0, 10) }}</v-btn></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></template><p v-else class="muted-copy">尚无已保存的压缩区间。</p>
          </v-card-text>
          <v-card-text v-else class="detail-body">
            <v-progress-linear v-if="recordsLoading" indeterminate /><v-alert v-if="recordsError" type="error" variant="tonal">{{ recordsError }}</v-alert><template v-if="records"><p class="muted-copy">只展示已持久化的明确关联。各类记录最多 50 项。</p><v-alert v-if="Object.values(records.truncated).some(Boolean)" type="info" variant="tonal">部分关联超出本页范围，可从对应对象继续查看。</v-alert><OperationReceipts :items="jobOperations" :scene-id="job.scene_id" /><h3>表达行动与真实回执</h3><article v-for="action in jobActions" :key="action.id" class="delivery-record"><code class="breakable">{{ action.id }}</code><div class="status-line"><v-chip v-if="action.acknowledges_task_id" size="small" variant="tonal">创建确认</v-chip><v-chip v-if="action.fulfils_task_id" size="small" variant="tonal">结果交付</v-chip><v-chip v-if="action.operation_ref" size="small" variant="tonal">操作确认 {{ action.operation_ref }}</v-chip><StatusBadge domain="delivery" :status="action.delivery_status" /><span v-if="action.job_revision">发送依据工作 v{{ action.job_revision }}</span></div><EntityLink v-if="action.origin_event_id" type="event" :id="action.origin_event_id" :scene-id="job.scene_id" label="本条表达对应的来源" /><div class="link-list"><EntityLink v-for="id in action.receipt_event_ids" :key="id" type="event" :id="id" :scene-id="job.scene_id" label="查看发送回执" /></div><p v-if="!action.receipt_event_ids.length" class="muted-copy">这条行动尚无已保存回执。</p></article><p v-if="!jobActions.length" class="muted-copy">未找到关联的表达行动。</p><h3>执行轨迹</h3><div class="link-list"><RouterLink v-for="item in records.traces" :key="item.id" :to="{ name: 'activity', query: { tab: 'turns', id: item.id, scene: job.scene_id } }">{{ item.kind }} · {{ item.id }}<span v-if="item.tool_outcomes?.errors"> · {{ item.tool_outcomes.errors }} 条工具错误</span></RouterLink></div><p v-if="!records.traces.length" class="muted-copy">没有关联轨迹。</p><h3>模型请求</h3><div class="link-list"><EntityLink v-for="item in records.calls" :key="item.id" type="call" :id="item.id" :scene-id="job.scene_id" :label="item.purpose + ' · ' + item.id" /></div><h3>原始事件与回执</h3><div class="link-list"><EntityLink v-for="item in records.events" :key="item.id" type="event" :id="item.id" :scene-id="job.scene_id" :label="item.event_type + ' · ' + item.id" /></div></template>
          </v-card-text>
        </v-card>
      </template>
    </template>
    <v-dialog :model-value="Boolean(confirmation)" :persistent="saving" max-width="620" @update:model-value="value => { if (!value && !saving) confirmation = null }"><v-card><v-card-title class="dialog-title">{{ confirmationTitle }}</v-card-title><v-card-text v-if="confirmation"><p>对象 {{ confirmation.id }} · 基于版本 {{ confirmation.expected_revision }}</p><p v-if="confirmation.operation === 'cancel'">停止此工作。已经取得的资料与历史结果会保留；不会自动重新执行。</p><p v-else-if="confirmation.operation === 'resume'">继续原工作的未完成部分，保留工作ID、已有资料、已用预算与绑定模型。旧版结果和交付回执保留，新结果使用新版本；本次不会增加预算。</p><template v-else><p class="full-title">{{ confirmation.goal || job.goal }}</p><ResourceViewer v-if="confirmation.parameters" title="本次业务参数变化" :content="confirmation.parameters" /><p>增加要求：{{ confirmation.constraints_add.join('；') || '无' }}</p><p>移除要求：{{ confirmation.constraints_remove.join('；') || '无' }}</p></template></v-card-text><v-card-actions class="dialog-actions"><v-btn variant="text" :disabled="saving" @click="confirmation = null">返回核对</v-btn><v-btn :color="confirmation?.operation === 'cancel' ? 'error' : 'primary'" :loading="saving" :disabled="saving" @click="submitAction">确认{{ confirmation?.operation === 'cancel' ? '停止' : confirmation?.operation === 'resume' ? '恢复' : '保存' }}</v-btn></v-card-actions></v-card></v-dialog>
  </section>
</template>

<style scoped>
.delivery-record,.adopted-range{display:grid;gap:10px;min-width:0;padding:14px 0;border-bottom:1px solid var(--line)}.evidence-range{display:flex;align-items:center;flex-wrap:wrap;gap:8px;font-size:13px}.adopted-range .v-btn{justify-self:start}.adopted-range p{margin:0;font-size:13px;overflow-wrap:anywhere}.delivery-record .breakable{white-space:normal}
.summary-facts{display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px 16px;margin:12px 0}.summary-facts dt{color:rgb(var(--v-theme-on-surface-variant))}.summary-facts dd{margin:0;overflow-wrap:anywhere}.summary-counts{margin:16px 0}@media(max-width:650px){.summary-facts{grid-template-columns:minmax(0,1fr);gap:4px}.summary-facts dd{margin-bottom:10px}}
.jobs-page{min-width:0}.jobs-page>.page-header{margin-bottom:24px}.section-gap{margin-top:20px}.filter-card{margin-bottom:20px}.job-filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr)) auto;gap:12px;align-items:center}.job-filters>:first-child{grid-column:1/2;width:100%}.job-filters>.v-text-field{grid-column:2/4}.list-meta{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0;color:rgb(var(--v-theme-on-surface-variant));font-size:13px}.work-list{display:grid;gap:12px}.work-row{padding:18px;display:grid;grid-template-columns:minmax(0,1fr) minmax(156px,180px) minmax(130px,180px) auto;gap:20px;align-items:center}.work-main{min-width:0;display:grid;gap:8px}.record-title{color:rgb(var(--v-theme-primary));font-size:15px;font-weight:600;line-height:1.55;text-decoration:none}.record-title:hover{text-decoration:underline}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.status-pair{display:grid;gap:7px}.status-pair>span{display:flex;gap:8px;align-items:center}.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.work-usage{display:grid;gap:8px;line-height:1.5}.full-title{white-space:pre-wrap;overflow-wrap:anywhere;font-size:20px;line-height:1.6;font-weight:600}.identity-line,.detail-status,.action-row,.status-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.identity-line,.detail-status{margin-top:12px}.action-row{margin-top:16px}.identity-line>*,.link-list>*{min-width:0;overflow-wrap:anywhere}.job-heading .read-time{margin-left:auto}.detail-body{min-width:0;line-height:1.65}.detail-body h3{font-size:17px;margin:24px 0 12px}.detail-body h3:first-child{margin-top:0}.detail-body h4{font-size:15px;margin:18px 0 8px}.detail-body ul,.detail-body ol{padding-left:24px;margin:8px 0}.detail-body li{margin:8px 0;overflow-wrap:anywhere}.detail-body p{margin:10px 0}.link-list{display:grid;gap:10px}.budget-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.budget-grid>div{padding:16px;background:rgb(var(--v-theme-surface-variant));border-radius:8px}.budget-grid span,.budget-grid strong{display:block}.budget-grid strong{font-size:20px;margin-top:8px}.resource-meta{font-size:13px}.source-url{display:block;overflow-wrap:anywhere}.breakable{overflow-wrap:anywhere}.empty-state{padding:20px;text-align:center}.dialog-title{white-space:normal;overflow-wrap:anywhere}.dialog-actions{flex-wrap:wrap;padding:16px}.edit-card{max-width:960px}@media(max-width:1150px){.work-row{grid-template-columns:minmax(0,1fr) auto;gap:16px}.work-main{grid-column:1/2}.status-pair{grid-column:1/2;grid-row:2;display:flex;flex-wrap:wrap}.work-usage{grid-column:1/2;grid-row:3;display:flex;flex-wrap:wrap}.work-row>.v-btn{grid-column:2;grid-row:1/4}}@media(max-width:650px){.job-filters{grid-template-columns:1fr}.job-filters>:first-child,.job-filters>.v-text-field{grid-column:auto}.work-row{grid-template-columns:minmax(0,1fr);padding:16px}.work-main,.status-pair,.work-usage,.work-row>.v-btn{grid-column:auto;grid-row:auto}.work-row>.v-btn{justify-self:start}.budget-grid{grid-template-columns:1fr}.job-heading .read-time{margin-left:0}.full-title{font-size:18px}.action-row>.v-btn{flex-grow:1}.identity-line{align-items:flex-start;flex-direction:column}}
</style>
