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
import PluginConfigFields from '../components/PluginConfigFields.vue'
import {configValue} from '../lib/pluginConfig.js'
import { hasConfigDraftChanges } from '../lib/configDraft.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import JobBudgetTab from '../components/jobs/JobBudgetTab.vue'
import JobProgressTab from '../components/jobs/JobProgressTab.vue'
import JobRecordsTab from '../components/jobs/JobRecordsTab.vue'
import JobResultTab from '../components/jobs/JobResultTab.vue'
import { useJobProgress } from '../components/jobs/useJobProgress.js'
import { useJobRecords } from '../components/jobs/useJobRecords.js'
import { useJobUsage } from '../components/jobs/useJobUsage.js'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const jobId = computed(() => scalar(route.params.jobId))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const tab = computed(() => ['result', 'progress', 'budget', 'records'].includes(route.query.tab) ? route.query.tab : 'result')
const filters = ref({ scene: '', status: '', execution: '', query: '' })
const rows = ref([]),
  total = ref(0),
  pageSize = ref(30),
  listLoading = ref(false),
  listError = ref(''),
  listLoaded = ref(false),
  listReadAt = ref(null)
const job = ref(null),
  detailLoading = ref(false),
  detailError = ref(''),
  detailMissing = ref(false),
  detailReadAt = ref(null)
const imageErrors=ref(new Set())
const editing = ref(false),
  draft = ref({ goal: '', constraints: '', parameters:{} }),
  baseline = ref(null),
  conflict = ref(false)
const confirmation = ref(null), saving = ref(false), actionError = ref(''), feedback = ref('')
const pendingControl=ref(null), controlReadAt=ref(null), controlReceipt=ref(null)
let listRequest = 0,
  detailRequest = 0
// Tab state is created here so it outlives tab switches; the page still
// decides when a job version's related state is loaded or reset.
const tabContext = {job, jobId, route, scalar, sameJob}
const progressTab = useJobProgress(tabContext)
const usageTab = useJobUsage(tabContext)
const recordsTab = useJobRecords(tabContext)
const { resource, loadWorkspaceArtifacts, loadResource, resetProgress, dropResource } = progressTab
const { usage, usagePage, loadUsage, resetUsage } = usageTab
const { records, loadRecords, resetRecords } = recordsTab
const shared = {job, jobId, route, scalar, imageErrors, openResource, clearFileFocus}
const actionGuard = useRequestGuard(() => JSON.stringify([route.name, jobId.value, route.query.scene]))
const clone = value => JSON.parse(JSON.stringify(value))
const constraintLines = value => [...new Set(value.split('\n').map(item=>item.trim()).filter(Boolean))]
function constraintChanges(original, text) {
  const next = constraintLines(text)
  return {
    add:next.filter(item=>!original.includes(item)),
    remove:original.filter(item=>!next.includes(item))
  }
}
const terminal = new Set(['completed', 'cancelled', 'delivery_unknown', 'shadow_observed'])
const editable = computed(() => job.value && !terminal.has(job.value.status))
const dirty = computed(() => {
  if (!editing.value || !baseline.value) return false
  const changes = constraintChanges(baseline.value.constraints, draft.value.constraints)
  return draft.value.goal.trim() !== baseline.value.goal || changes.add.length > 0 || changes.remove.length > 0 || Object.keys(draft.value.parameters).length > 0
})
const { confirmLeave } = useUnsavedChanges(dirty)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const executionOptions = [
  { title: '全部执行状态', value: '' },
  { title: '尚未开始', value: 'pending' },
  { title: '执行中', value: 'running' },
  { title: '执行完成', value: 'completed' },
  { title: '部分完成', value: 'partial' },
  { title: '执行失败', value: 'failed' },
  { title: '执行中断', value: 'interrupted' },
  { title: '执行取消', value: 'cancelled' }
]
const deliveryOptions = [
  { title: '全部任务状态', value: '' },
  { title: '等待执行', value: 'pending' },
  { title: '处理中', value: 'processing' },
  { title: '结果待回应', value: 'result_ready' },
  { title: '等待送达', value: 'awaiting_delivery' },
  { title: '任务已完成（交付另见回执）', value: 'completed' },
  { title: '中断待核对', value: 'review_required' },
  { title: '送达未知', value: 'delivery_unknown' },
  { title: '任务失败', value: 'failed' },
  { title: '仅观察', value: 'shadow_observed' },
  { title: '已取消', value: 'cancelled' }
]
function sameJob(current) {
  return jobId.value === current.id && job.value?.id === current.id && job.value?.scene_id === current.scene_id && job.value?.revision === current.revision
}
function resetRelated() {
  resetRecords()
  resetProgress()
  resetUsage()
}
function cleanQuery(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '' && value !== null && value !== undefined))
}
function listQuery() {
  const { tab: ignoredTab, resource: ignoredResource, file: ignoredFile, list_scene: originalScene, ...values } = route.query;
  if (originalScene !== undefined) values.scene = scalar(originalScene) || undefined;
  return values
}
function clearFileFocus() {
  const query = { ...route.query };
  delete query.file;
  router.replace({ name: 'job', params: { jobId: jobId.value }, query })
}
function openJob(item) {
  router.push({
    name: 'job',
    params: { jobId: item.id },
    query: {
      ...listQuery(),
      list_scene: scalar(route.query.scene),
      scene: item.scene_id,
      tab: 'result'
    }
  })
}
function backToList() {
  router.push({ name: 'jobs', query: listQuery() })
}
function setTab(value) {
  router.replace({
    name: 'job',
    params: { jobId: jobId.value },
    query: { ...route.query, tab: value }
  })
}
function applyFilters() {
  router.push({
    name: 'jobs',
    query: cleanQuery({
      return_to: route.query.return_to,
      scene: filters.value.scene,
      status: filters.value.status,
      execution: filters.value.execution,
      query: (filters.value.query || '').trim(),
      page: 1
    })
  })
}
function setPage(value) {
  router.push({ name: 'jobs', query: { ...route.query, page: value } })
}

async function loadList() {
  const request = ++listRequest
  listLoading.value = true;
  listError.value = ''
  const params = new URLSearchParams(cleanQuery({
    scene_id: scalar(route.query.scene),
    status: scalar(route.query.status),
    execution_status: scalar(route.query.execution),
    query: scalar(route.query.query),
    page: page.value,
    page_size: 30
  }))
  try {
    const result = await api('/api/cockpit/jobs?' + params)
    if (request !== listRequest || jobId.value) return
    rows.value = result.items;
    total.value = result.total;
    pageSize.value = result.page_size
    listLoaded.value = true;
    listReadAt.value = Date.now() / 1000
  } catch (error) {
    if (request === listRequest) listError.value = error.message
  }
  finally {
    if (request === listRequest) listLoading.value = false
  }
}
async function loadJob({ reset = false, accept = () => true } = {}) {
  const id = jobId.value, request = ++detailRequest, scope = scalar(route.query.scene)
  const fresh = () => request === detailRequest && id === jobId.value && scope === scalar(route.query.scene) && accept()
  if (!id) return
  detailLoading.value = true;
  detailError.value = '';
  detailMissing.value = false
  if(pendingControl.value)controlReadAt.value=null
  if (reset) {
    job.value = null;
    detailReadAt.value = null;
    resetRelated()
  }
  try {
    const value = await api(`/api/cockpit/jobs/${encodeURIComponent(id)}` + (scope ? '?scene_id=' + encodeURIComponent(scope) : ''))
    if (!fresh()) return
    if(value.id!==id||(scope&&value.scene_id!==scope))throw new Error('当前工作读取的身份与目标不符，未采用。')
    if(pendingControl.value&&value.scene_id!==pendingControl.value.scene)throw new Error('当前工作不属于原控制场景，未采用。')
    if (job.value && value.revision !== job.value.revision) resetRelated()
    job.value = value;
    imageErrors.value=new Set();
    detailReadAt.value = Date.now() / 1000
    if(pendingControl.value)controlReadAt.value=detailReadAt.value
    if (editing.value && baseline.value && (value.revision !== baseline.value.revision || changedWorkContract())) conflict.value = true
    await Promise.all([loadWorkspaceArtifacts(), tab.value === 'records' ? loadRecords() : null,
      tab.value === 'budget' ? loadUsage(usagePage.value) : null,
      scalar(route.query.resource) ? loadResource(scalar(route.query.resource)) : null])
  } catch (error) {
    if (fresh()) {
      detailError.value = error.message;
      detailMissing.value = error.status === 404;
      if (detailMissing.value) job.value = null
    }
  }
  finally {
    if (fresh()) detailLoading.value = false
  }
}
function openResource(id) {
  router.replace({
    name: 'job',
    params: { jobId: jobId.value },
    query: { ...route.query, tab: 'progress', resource: id }
  })
}
function currentBaseline() {
  return { id:job.value.id, scene:job.value.scene_id, revision:job.value.revision, goal:job.value.goal, constraints:[...job.value.constraints],
    parameters:clone(job.value.work_parameters ?? null), schema:clone(job.value.work_revision_schema ?? null),
    origin:clone(job.value.plugin_origin ?? null), operation:job.value.work_operation }
}
function changedWorkContract() {
  return baseline.value && job.value && (baseline.value.operation !== job.value.work_operation
    || hasConfigDraftChanges(baseline.value.origin, job.value.plugin_origin ?? null)
    || hasConfigDraftChanges(baseline.value.schema, job.value.work_revision_schema ?? null))
}
function startEdit() {
  if (pendingControl.value || !editable.value || saving.value || detailLoading.value || detailError.value) return
  baseline.value = currentBaseline()
  draft.value = {
    goal: job.value.goal,
    constraints: job.value.constraints.join('\n'),
    parameters:{}
  }
  conflict.value = false;
  actionError.value = '';
  feedback.value = '';
  editing.value = true
}
function cancelEdit() {
  if (!saving.value && confirmLeave()) {
    editing.value = false;
    baseline.value = null;
    conflict.value = false
  }
}
function discardParameterChanges() {
  if (saving.value || !window.confirm('只放弃本次插件业务参数修改？工作目标和要求草稿保留。')) return
  draft.value.parameters = {};
  actionError.value = ''
}
function reviewLatestVersion(keep = true) {
  if (saving.value || pendingControl.value || detailLoading.value || detailError.value || !job.value || !baseline.value || !editable.value) return
  if (job.value.id !== baseline.value.id || job.value.scene_id !== baseline.value.scene) return
  if (!keep && !window.confirm('放弃本页未保存的工作修订，采用刚读到的目标、要求和业务参数？')) return
  if (keep && Object.keys(draft.value.parameters).length && changedWorkContract()) {
    actionError.value = '业务修订接口已经改变，不能将原参数草稿套到新接口。请先放弃业务参数修改，或采用现值后重新填写；目标和要求草稿仍保留。'
    return
  }
  const changes = constraintChanges(baseline.value.constraints, draft.value.constraints)
  const next = keep ? {
    goal:draft.value.goal.trim() === baseline.value.goal ? job.value.goal : draft.value.goal,
    constraints:[
      ...new Set([
        ...job.value.constraints.filter(item=>!changes.remove.includes(item)),
        ...changes.add
      ])
    ].join('\n'),
    parameters:clone(draft.value.parameters),
  } : {
    goal:job.value.goal,
    constraints:job.value.constraints.join('\n'),
    parameters:{}
  }
  baseline.value = currentBaseline();
  draft.value = next
  conflict.value = false
  actionError.value = ''
  feedback.value = keep ? `已将实际改动重建到版本 ${job.value.revision}；未编辑的目标和要求采用现值。请核对后再保存，尚未提交。` : `已采用版本 ${job.value.revision}，没有提交修订。`
}
function askAction(operation) {
  if (saving.value || pendingControl.value || detailLoading.value || detailError.value || !job.value) return
  if (operation === 'cancel' && !editable.value || operation === 'resume' && !job.value.can_resume) return
  const current = job.value
  if (operation === 'revise') {
    if (!editable.value || !draft.value.goal.trim() || !baseline.value || conflict.value) return
    const changes = constraintChanges(baseline.value.constraints, draft.value.constraints)
    let parameters=null
    try {
      if (Object.keys(draft.value.parameters).length) {
        if (!baseline.value.schema || changedWorkContract()) throw new Error('业务修订接口缺失或已改变，未忽略参数草稿。请先核对当前工作和修订接口。')
        parameters=configValue(draft.value.parameters,baseline.value.schema)
      }
    }
    catch(error) {
      actionError.value=error.message;
      return
    }
    confirmation.value = {
      operation,
      id: current.id,
      scene: current.scene_id,
      expected_revision: baseline.value.revision,
      displayGoal:draft.value.goal.trim(),
      goal: draft.value.goal.trim()===baseline.value.goal?null:draft.value.goal.trim(),
      parameters,
      constraints_add: changes.add,
      constraints_remove: changes.remove
    }
  } else {
    confirmation.value = {
      operation,
      id: current.id,
      scene: current.scene_id,
      expected_revision: current.revision,
      goal: null,
      constraints_add: [],
      constraints_remove: []
    }
  }
  actionError.value = ''
}
const confirmationTitle = computed(() => ({ revise: '确认修改工作要求', resume: '确认恢复执行', cancel: '确认停止工作' }[confirmation.value?.operation] || '确认操作'))
function sameControl(value,attempt) {
  return value?.job_id===attempt.id&&value.scene_id===attempt.scene&&value.operation===attempt.operation
    && value.expected_revision===attempt.expected_revision
}
function continueFromCurrent() {
  if(saving.value||detailLoading.value||detailError.value||!pendingControl.value||controlReadAt.value===null)return
  if(!window.confirm('放弃原控制确认和修订草稿，按当前工作重新选择操作？这不重发、撤销或追认旧请求，也不补充预算。'))return
  pendingControl.value=null;
  controlReadAt.value=null;
  confirmation.value=null
  editing.value=false;
  baseline.value=null;
  conflict.value=false;
  actionError.value=''
  feedback.value='已结束原控制草稿，请按当前工作重新选择；原操作与外部停止／发送结果仍需分别核对。'
}
async function submitAction() {
  if (saving.value || pendingControl.value || detailLoading.value || detailError.value || !confirmation.value || !job.value) return
  const attempt=clone(confirmation.value)
  const { operation, id, scene, displayGoal, ...body } = attempt
  if (id !== job.value.id || scene !== job.value.scene_id || body.expected_revision !== job.value.revision) {
    actionError.value = '确认期间工作已变化，未提交旧确认。请核对当前版本后重新选择操作。'
    if (operation === 'revise') conflict.value = true
    confirmation.value = null;
    return
  }
  const fresh = actionGuard()
  saving.value = true;
  ++detailRequest;
  detailLoading.value = false;
  actionError.value = '';
  feedback.value = '';
  controlReceipt.value=null
  let submitted=false, accepted=false
  try {
    const payload=JSON.stringify(body)
    submitted=true
    const result = await api(`/api/cockpit/jobs/${encodeURIComponent(id)}/${operation}`, { method: 'POST', body: payload })
    if (!fresh()) return
    if(result.success!==true||!sameControl(result,attempt)||result.control_accepted!==true)throw new Error('控制响应缺少原操作的明确提交确认，结果待核对。')
    accepted=true;
    controlReceipt.value=result
    if (!result.job || result.job.id !== id || result.job.scene_id !== scene) throw new Error('工作控制已提交，但未返回同一工作的保存值；请读取当前工作，不重复提交。')
    resetRelated();
    job.value = result.job;
    detailReadAt.value = Date.now() / 1000;
    confirmation.value = null
    if (operation === 'revise') {
      editing.value = false;
      baseline.value = null;
      conflict.value = false
    }
    feedback.value = {
      revise: '要求已保存，工作版本已更新。',
      resume: '恢复请求已提交，预算和模型绑定保留。',
      cancel: '取消请求已提交；实际执行停止状态和历史记录请继续核对。'
    }[operation]
    await Promise.all([
      loadWorkspaceArtifacts(),
      tab.value === 'budget' ? loadUsage() : null,
      tab.value === 'records' ? loadRecords() : null
    ])
  } catch (error) {
    if (!fresh()) return
    actionError.value = error.message;
    confirmation.value = null
    const matching=sameControl(error.details,attempt)
    if(matching)controlReceipt.value=error.details
    if(matching&&error.details.control_accepted===false){
      if(operation==='revise')conflict.value=true
      await loadJob({accept:fresh})
    }else if(submitted&&!(error.status===422&&Array.isArray(error.details))){
      pendingControl.value={
        ...attempt,
        accepted:accepted||(matching&&error.details.control_accepted===true)
      }
      controlReadAt.value=null
      await loadJob({accept:fresh})
    }
  } finally {
    if (fresh()) saving.value = false
  }
}
function refresh() {
  if (saving.value) return;
  return jobId.value ? loadJob() : loadList()
}
function onVisible() {
  if (document.visibilityState === 'visible') refresh()
}
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => {
  ++listRequest;
  ++detailRequest;
  resetRelated();
  document.removeEventListener('visibilitychange', onVisible)
})
onBeforeRouteUpdate((to, from) => to.params.jobId !== from.params.jobId || to.query.scene !== from.query.scene ? confirmLeave() : true)
watch(() => [route.params.jobId, route.query.scene], () => {
  ++listRequest;
  ++detailRequest;
  actionGuard();
  saving.value = false
  editing.value = false;
  baseline.value = null;
  conflict.value = false;
  confirmation.value = null;
  actionError.value = '';
  feedback.value = ''
  pendingControl.value=null;
  controlReadAt.value=null;
  controlReceipt.value=null
  if (jobId.value) loadJob({ reset: true })
  else {
    job.value = null;
    resetRelated()
  }
}, { immediate: true, flush:'sync' })
watch(() => [
  route.query.scene,
  route.query.status,
  route.query.execution,
  route.query.query,
  route.query.page,
  route.params.jobId
], () => {
  filters.value = {
    scene: scalar(route.query.scene),
    status: scalar(route.query.status),
    execution: scalar(route.query.execution),
    query: scalar(route.query.query)
  }
  if (!jobId.value) {
    rows.value = [];
    total.value = 0;
    listLoaded.value = false;
    listReadAt.value = null;
    loadList()
  }
}, { immediate: true })
watch(tab, value => {
  if (value === 'records' && job.value && !records.value) loadRecords();
  if (value === 'budget' && job.value && !usage.value) loadUsage()
})
watch(() => route.query.resource, value => {
  if (value && job.value) loadResource(scalar(value)); else dropResource()
})
</script>

<template>
  <section class="jobs-page">
    <PageHeader :title="jobId ? '工作详情' : '信息工作'" description="执行结果与消息送达分别记录；已有资料和版本可以回查。">
      <v-btn v-if="jobId" variant="text" :prepend-icon="mdiArrowLeft" @click="backToList">返回工作列表</v-btn>
      <v-btn
        variant="outlined"
        :prepend-icon="mdiRefresh"
        :loading="jobId ? detailLoading : listLoading"
        @click="refresh"
      >刷新</v-btn>
    </PageHeader>
    <p v-if="jobId" class="muted-copy">离开或切换工作只停止本页跟踪，不撤销已提交控制。取消请求、工作状态和外部执行是否停止分别核对，不能将提交成功当作全部执行已经终止。</p>
    <template v-if="!jobId">
      <v-card class="filter-card">
        <v-card-text>
          <v-form class="job-filters" @submit.prevent="applyFilters">
            <ScopeSelect v-model="filters.scene" clearable />
            <v-text-field v-model="filters.query" label="查找工作目标" hide-details clearable />
            <v-select
              v-model="filters.execution"
              :items="executionOptions"
              label="执行状态"
              hide-details
            />
            <v-select
              v-model="filters.status"
              :items="deliveryOptions"
              label="任务状态（含无需群交付）"
              hide-details
            />
            <v-btn type="submit" color="primary">筛选</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
      <v-alert v-if="listError" type="error" variant="tonal" title="工作列表读取失败" class="section-gap">
        {{ listError }}<div v-if="listReadAt">保留上次读取结果：{{ fmtTime(listReadAt) }}</div>
      </v-alert>
      <v-progress-linear v-if="listLoading" indeterminate class="section-gap" aria-label="正在读取工作" />
      <div v-if="listLoaded" class="list-meta">
        <span>共 {{ total }} 项 · 每页 {{ pageSize }} 项</span>
        <span>读取于 {{ fmtTime(listReadAt) }}</span>
      </div>
      <div class="work-list">
        <v-card v-for="item in rows" :key="item.id" tag="article" class="work-row">
          <div class="work-main">
            <RouterLink
              :to="{ name: 'job', params: { jobId: item.id }, query: { ...listQuery(), list_scene: scalar(route.query.scene), scene: item.scene_id, tab: 'result' } }"
              class="two-lines record-title"
            >
              {{ item.goal }}
            </RouterLink>
            <EntityLink type="scene" :id="item.scene_id" :scene-id="item.scene_id" />
            <span v-if="item.plugin_origin" class="muted-copy">
              {{ item.plugin_name || item.plugin_origin.plugin_id }} · {{ item.plugin_origin.plugin_version }}
            </span>
          </div>
          <div class="status-pair">
            <span>
              <span class="field-label">执行</span>
              <StatusBadge domain="job_execution" :status="item.execution_status" />
            </span>
            <span>
              <span class="field-label">交付</span>
              <StatusBadge
                domain="job_delivery"
                :status="item.delivery_required === false ? 'not_required' : item.status"
              />
            </span>
          </div>
          <div class="work-usage">
            <span>模型 {{ item.model_steps }} · 工具 {{ item.tool_calls }}</span>
            <time>{{ fmtTime(item.updated_at) }}</time>
          </div>
          <v-btn variant="tonal" @click="openJob(item)">查看详情</v-btn>
        </v-card>
      </div>
      <v-card v-if="listLoaded && !rows.length && !listError" class="empty-state">
        <v-card-text>没有符合筛选条件的工作。</v-card-text>
      </v-card>
      <v-pagination
        v-if="pages > 1"
        :model-value="page"
        :length="pages"
        :total-visible="5"
        class="section-gap"
        @update:model-value="setPage"
      />
    </template>
    <template v-else>
      <v-alert
        v-if="detailError"
        :type="detailMissing ? 'warning' : 'error'"
        variant="tonal"
        :title="detailMissing ? '工作不存在或不属于此场景' : '工作详情读取失败'"
      >
        {{ detailError }}<div v-if="detailReadAt">上次读取：{{ fmtTime(detailReadAt) }}</div>
      </v-alert>
      <v-skeleton-loader v-if="detailLoading && !job" type="article, list-item-three-line" />
      <v-card v-if="editing&&!job" class="section-gap">
        <v-card-text>
          <p>当前工作详情不可读取，原版本 {{ baseline.revision }} 的修订草稿仍保留，未套用到其他工作。</p>
          <ResourceViewer title="未保存的工作修订" :content="draft" />
          <v-btn variant="text" :disabled="saving" @click="cancelEdit">放弃修订草稿</v-btn>
        </v-card-text>
      </v-card>
      <v-alert v-if="feedback" type="success" variant="tonal" class="section-gap" role="status">
        {{ feedback }}
      </v-alert>
      <v-alert v-if="actionError" type="error" variant="tonal" class="section-gap" role="alert">
        {{ actionError }}
      </v-alert>
      <v-alert v-if="pendingControl" type="warning" variant="tonal" class="section-gap">
        <p>
          {{ pendingControl.accepted?'原控制已取得提交确认，当前工作仍需核对。':'原控制结果未知。' }}{{ pendingControl.operation }} · {{ pendingControl.id }} · 基于版本 {{ pendingControl.expected_revision }}。不能以再次修订、恢复或取消代替核对。</p>
        <p v-if="controlReadAt!==null">当前工作已于 {{ fmtTime(controlReadAt) }} 读取；当前状态不是原请求回执。</p>
        <v-btn variant="text" :disabled="saving||detailLoading" @click="refresh">读取当前工作</v-btn>
        <v-btn
          variant="text"
          :disabled="saving||detailLoading||!!detailError||controlReadAt===null"
          @click="continueFromCurrent"
        >结束原确认，按当前工作操作</v-btn>
      </v-alert>
      <div v-if="controlReceipt" class="identity-line section-gap">
        <EntityLink
          v-if="controlReceipt.source_event_id"
          type="event"
          :id="controlReceipt.source_event_id"
          :scene-id="controlReceipt.scene_id"
          label="本次控制的管理来源"
        />
        <EntityLink
          v-if="controlReceipt.commit_event_id"
          type="event"
          :id="controlReceipt.commit_event_id"
          :scene-id="controlReceipt.scene_id"
          label="本次控制的提交记录"
        />
      </div>
      <template v-if="job">
        <v-card class="job-heading">
          <v-card-text>
            <h2 class="full-title">{{ job.goal }}</h2>
            <div class="identity-line">
              <EntityLink type="job" :id="job.id" :scene-id="job.scene_id" />
              <EntityLink type="scene" :id="job.scene_id" :scene-id="job.scene_id" />
              <span>目标版本 {{ job.revision }}</span>
            </div>
            <div class="identity-line">
              <span>发起人 {{ job.initiator?.principal_type === 'human' ? '用户 ' + job.initiator.user_id : job.initiator?.principal_type === 'system' ? '系统 ' + job.initiator.agent_id : job.initiator?.principal_type === 'plugin' ? '插件 ' + job.initiator.plugin_id : '未记录' }}
              </span>
              <span>付额账户 {{ job.reservation?.subject || '未记录' }}</span>
              <EntityLink
                v-if="job.request_source_event_id"
                type="event"
                :id="job.request_source_event_id"
                :scene-id="job.scene_id"
                label="发起此工作的来源事件"
              />
              <span v-else class="muted-copy">旧工作未单独保存请求来源</span>
            </div>
            <div class="detail-status">
              <span>执行 <StatusBadge domain="job_execution" :status="job.execution_status" /></span>
              <span>交付 <StatusBadge
                  domain="job_delivery"
                  :status="job.delivery_required === false ? 'not_required' : job.status"
                />
              </span>
              <span class="read-time">读取于 {{ fmtTime(detailReadAt) }}</span>
            </div>
            <div class="action-row">
              <v-btn
                :disabled="!editable || job.plugin_issue || saving || editing || detailLoading || !!detailError || !!pendingControl"
                :prepend-icon="mdiPencilOutline"
                variant="outlined"
                @click="startEdit"
              >修改要求</v-btn>
              <v-btn
                :disabled="!job.can_resume || saving || editing || detailLoading || !!detailError || !!pendingControl"
                :prepend-icon="mdiPlayOutline"
                variant="outlined"
                @click="askAction('resume')"
              >
                {{ job.execution_status==='partial'?'继续未完成部分':'核对后恢复' }}
              </v-btn>
              <v-btn
                :disabled="!editable || saving || editing || detailLoading || !!detailError || !!pendingControl"
                :prepend-icon="mdiStopCircleOutline"
                color="error"
                variant="outlined"
                @click="askAction('cancel')"
              >停止工作</v-btn>
            </div>
          </v-card-text>
        </v-card>
        <v-card v-if="job.reused_work || job.followup_work?.total" class="section-gap">
          <v-card-title>成果复用关系</v-card-title>
          <v-card-text>
            <template v-if="job.reused_work">
              <p>本工作固定使用原成果版本 {{ job.reused_work.revision }}，后续原工作变化不会替换下方输入。新要求、申请者、额度及交付仍归本工作。</p>
              <div class="identity-line">
                <EntityLink
                  type="job"
                  :id="job.reused_work.job_id"
                  :scene-id="job.scene_id"
                  label="查看原工作当前详情"
                />
                <span>所选版本的执行结果 <StatusBadge domain="job_execution" :status="job.reused_work.status" />
                </span>
                <EntityLink
                  v-if="job.reused_work.request_source_event_id"
                  type="event"
                  :id="job.reused_work.request_source_event_id"
                  :scene-id="job.scene_id"
                  label="原研究的请求来源"
                />
              </div>
              <details class="mt-3">
                <summary>查看固定的原成果输入</summary>
                <ResourceViewer :content="job.reused_work" title="原工作整理结果及资料位置（不是本工作的已读证明）" />
              </details>
            </template>
            <template v-if="job.followup_work?.total">
              <h3 class="mt-4">复用此成果的后续工作</h3>
              <p class="muted-copy">显示最近 {{ job.followup_work.items.length }} / {{ job.followup_work.total }} 项明确保存的复用关系；不会从目标文字或相同资料推断旧工作关系。</p>
              <div v-for="item in job.followup_work.items" :key="item.id" class="identity-line">
                <EntityLink type="job" :id="item.id" :scene-id="job.scene_id" :label="item.goal" />
                <span>复用版本 {{ item.source_revision }} · 自身版本 {{ item.revision }}</span>
                <StatusBadge domain="job_delivery" :status="item.status" />
              </div>
            </template>
          </v-card-text>
        </v-card>
        <v-card v-if="editing" class="section-gap edit-card">
          <v-card-title>修改要求 · 基于版本 {{ baseline.revision }}</v-card-title>
          <v-card-text>
            <v-alert v-if="conflict" type="warning" variant="tonal" class="mb-4">
              <p>修订被拒绝或读取到了新的版本／业务接口，草稿仍保留。以下是 {{ fmtTime(detailReadAt) }} 读到的版本 {{ job.revision }}，不是自动更新后的编辑基线。</p>
              <p v-if="detailError" class="mt-2">本次刷新失败，尚不能据旧样本选择新基线，请先刷新工作。</p>
              <ResourceViewer title="本次读取的目标" :content="job.goal" class="mt-3" />
              <ResourceViewer title="本次读取的要求" :content="job.constraints" class="mt-3" />
              <details v-if="Object.keys(draft.parameters).length" class="mt-3">
                <summary>核对业务参数和本次修改</summary>
                <ResourceViewer title="原编辑基线的业务参数" :content="baseline.parameters" />
                <ResourceViewer title="本次读取的业务参数" :content="job.work_parameters" />
                <ResourceViewer title="尚未提交的参数修改" :content="draft.parameters" />
              </details>
              <p class="mt-3">保留只重建实际编辑的目标与要求增删，其他人的新增要求不会变成删除。参数修改仍交给原插件基于明确选择的新版本解释；选择本身不提交。</p>
              <div class="action-row">
                <v-btn
                  variant="outlined"
                  :disabled="!editable||saving||detailLoading||!!detailError || !!pendingControl"
                  @click="reviewLatestVersion(true)"
                >保留实际改动，采用新基线</v-btn>
                <v-btn
                  variant="text"
                  :disabled="!editable||saving||detailLoading||!!detailError || !!pendingControl"
                  @click="reviewLatestVersion(false)"
                >放弃草稿，采用现值</v-btn>
              </div>
            </v-alert>
            <v-textarea v-model="draft.goal" label="工作目标" rows="2" auto-grow :disabled="saving" />
            <v-textarea
              v-model="draft.constraints"
              label="要求（每行一项）"
              rows="4"
              auto-grow
              :disabled="saving"
            />
            <template v-if="baseline.schema">
              <h4>修改插件业务参数</h4>
              <p class="muted-copy">只填写需要改变的字段；未填写字段由所属插件保留。本表单仍使用开始编辑时的修订接口，范围和快照由所属插件处理。</p>
              <PluginConfigFields
                v-model="draft.parameters"
                :schema="baseline.schema"
                :disabled="saving"
              />
            </template>
            <v-btn
              v-if="Object.keys(draft.parameters).length"
              class="mt-3"
              variant="text"
              :disabled="saving"
              @click="discardParameterChanges"
            >只放弃业务参数修改</v-btn>
            <div class="action-row">
              <v-btn
                color="primary"
                :disabled="!editable || !dirty || !draft.goal.trim() || conflict || saving || detailLoading || !!detailError || !!pendingControl"
                @click="askAction('revise')"
              >保存修改</v-btn>
              <v-btn variant="text" :disabled="saving" @click="cancelEdit">取消编辑</v-btn>
            </div>
          </v-card-text>
        </v-card>
        <v-card class="section-gap">
          <v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="setTab">
            <v-tab value="result">结果</v-tab>
            <v-tab value="progress">进度与资料</v-tab>
            <v-tab value="budget">预算与压缩</v-tab>
            <v-tab value="records">执行记录</v-tab>
          </v-tabs>
          <JobResultTab v-if="tab === 'result'" :page="shared" />
          <JobProgressTab v-else-if="tab === 'progress'" :state="progressTab" :page="shared" />
          <JobBudgetTab v-else-if="tab === 'budget'" :state="usageTab" :page="shared" />
          <JobRecordsTab v-else :state="recordsTab" :page="shared" />
        </v-card>
      </template>
    </template>
    <v-dialog
      :model-value="Boolean(confirmation)"
      :persistent="saving"
      max-width="620"
      @update:model-value="value => { if (!value && !saving) confirmation = null }"
    >
      <v-card>
        <v-card-title class="dialog-title">{{ confirmationTitle }}</v-card-title>
        <v-card-text v-if="confirmation">
          <p>对象 {{ confirmation.id }} · 基于版本 {{ confirmation.expected_revision }}</p>
          <v-alert
            v-if="!job||job.revision!==confirmation.expected_revision||detailError"
            type="warning"
            variant="tonal"
            class="my-3"
          >当前工作已变化或读取失败，请返回核对。此确认仍是原版本的操作，不自动迁移。</v-alert>
          <p v-if="confirmation.operation === 'cancel'">停止此工作。已经取得的资料与历史结果会保留；不会自动重新执行。</p>
          <p v-else-if="confirmation.operation === 'resume'">继续原工作的未完成部分，保留工作ID、已有资料、已用预算与绑定模型。旧版结果和交付回执保留，新结果使用新版本；本次不会增加预算。</p>
          <template v-else>
            <p class="full-title">{{ confirmation.displayGoal }}</p>
            <ResourceViewer
              v-if="confirmation.parameters"
              title="本次业务参数变化"
              :content="confirmation.parameters"
            />
            <p>增加要求：{{ confirmation.constraints_add.join('；') || '无' }}</p>
            <p>移除要求：{{ confirmation.constraints_remove.join('；') || '无' }}</p>
          </template>
        </v-card-text>
        <v-card-actions class="dialog-actions">
          <v-btn variant="text" :disabled="saving" @click="confirmation = null">返回核对</v-btn>
          <v-btn
            :color="confirmation?.operation === 'cancel' ? 'error' : 'primary'"
            :loading="saving"
            :disabled="saving || detailLoading || !!detailError"
            @click="submitAction"
          >确认{{ confirmation?.operation === 'cancel' ? '停止' : confirmation?.operation === 'resume' ? '恢复' : '保存' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
<style scoped>
.summary-counts{margin:16px 0}
.jobs-page{min-width:0}
.jobs-page>.page-header{margin-bottom:24px}
.section-gap{margin-top:20px}
.filter-card{margin-bottom:20px}
.job-filters{display:flex;align-items:stretch;gap:12px;flex-wrap:wrap}
.job-filters>*{flex:1 1 210px;min-width:0}
.job-filters>.scope-select{flex-basis:220px}
.job-filters>.v-btn{flex:0 0 96px;align-self:center}
.job-filters :deep(.scope-select){width:auto;max-width:none}
.list-meta{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0;color:rgb(var(--v-theme-on-surface-variant));font-size:13px}
.work-list{display:grid;gap:12px}
.work-row{padding:18px;display:grid;grid-template-columns:minmax(0,1fr) minmax(156px,180px) minmax(130px,180px) auto;gap:20px;align-items:center}
.work-main{min-width:0;display:grid;gap:8px}
.record-title{color:rgb(var(--v-theme-primary));font-size:15px;font-weight:600;line-height:1.55;text-decoration:none}
.record-title:hover{text-decoration:underline}
.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.status-pair{display:grid;gap:7px}
.status-pair>span{display:flex;gap:8px;align-items:center}
.field-label,.work-usage,.muted-copy,.read-time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}
.work-usage{display:grid;gap:8px;line-height:1.5}
.full-title{white-space:pre-wrap;overflow-wrap:anywhere;font-size:20px;line-height:1.6;font-weight:600}
.identity-line,.detail-status,.action-row,.status-line{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.identity-line,.detail-status{margin-top:12px}
.action-row{margin-top:16px}
.identity-line>*,.link-list>*{min-width:0;overflow-wrap:anywhere}
.job-heading .read-time{margin-left:auto}
.source-url{display:block;overflow-wrap:anywhere}
.empty-state{padding:20px;text-align:center}
.dialog-title{white-space:normal;overflow-wrap:anywhere}
.dialog-actions{flex-wrap:wrap;padding:16px}
.edit-card{max-width:960px}
@media(max-width:1150px){
  .work-row{grid-template-columns:minmax(0,1fr) auto;gap:16px}
  .work-main{grid-column:1/2}
  .status-pair{grid-column:1/2;grid-row:2;display:flex;flex-wrap:wrap}
  .work-usage{grid-column:1/2;grid-row:3;display:flex;flex-wrap:wrap}
  .work-row>.v-btn{grid-column:2;grid-row:1/4}
}
@media(max-width:650px){
  .job-filters{display:grid;grid-template-columns:1fr}
  .job-filters>*{width:100%;flex:auto}
  .job-filters>.v-btn{width:100%}
  .work-row{grid-template-columns:minmax(0,1fr);padding:16px}
  .work-main,.status-pair,.work-usage,.work-row>.v-btn{grid-column:auto;grid-row:auto}
  .work-row>.v-btn{justify-self:start}
  .job-heading .read-time{margin-left:0}
  .full-title{font-size:18px}
  .action-row>.v-btn{flex-grow:1}
  .identity-line{align-items:flex-start;flex-direction:column}
}
</style>
