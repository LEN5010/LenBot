<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { mdiArrowLeft, mdiRefresh, mdiPencilOutline, mdiClockFast, mdiCancel } from '@mdi/js'
import { api } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { hasConfigDraftChanges } from '../lib/configDraft.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const tab = computed(() => ['waiting', 'system', 'deferred'].includes(route.query.tab) ? route.query.tab : 'reminders')
const id = computed(() => scalar(route.query.id))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const routeStatus = computed(() => route.query.status === undefined ? (tab.value === 'waiting' ? 'active' : '') : scalar(route.query.status))
const filters = ref({ scene: '', status: '' })
const rows = ref([]), total = ref(0), pageSize = ref(30), loading = ref(false), listError = ref(''), loaded = ref(false), readAt = ref(null)
const detail = ref(null), detailLoading = ref(false), detailError = ref(''), detailMissing = ref(false), detailReadAt = ref(null)
const editing = ref(false), draft = ref({ description: '', time: '' }), baseline = ref(null), confirmation = ref(null)
const timeAnchor = ref(null)
const saving = ref(false), actionError = ref(''), feedback = ref('')
const conflict = ref(false), readbackPending = ref('')
const selection = () => JSON.stringify([route.name, id.value, route.query.scene, tab.value])
const listGuard = useRequestGuard(() => JSON.stringify([selection(), routeStatus.value, page.value]))
const detailGuard = useRequestGuard(selection), actionGuard = useRequestGuard(selection)
const clone = value => JSON.parse(JSON.stringify(value))
const reminderFields = ['id','scene_id','description','due_at','status','created_at','source_event_id','wake_event_type','wake_match','trigger_event_id']
const waitingFields = ['id','scene_id','status','target_actor_id','source_event_id','intent','created_at','expires_at']
const selectedFacts = (item, waiting = false) => clone(Object.fromEntries((waiting ? waitingFields : reminderFields).map(key => [key,item[key]])))
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
function fmtTime(timestamp) { return timestamp === null || timestamp === undefined ? '—' : Number.isFinite(new Date(timestamp * 1000).getTime()) ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) : `${timestamp} Unix 秒（超出日期可表示范围）` }
function absoluteTime(timestamp) { const value=new Date(timestamp*1000); return Number.isFinite(value.getTime()) ? value.toISOString() : `${timestamp} Unix 秒（超出日期可表示范围）` }
const isWork = computed(() => detail.value?.payload?.kind === 'agent_job')
const isSystem = computed(() => ['heartbeat', 'heartbeat_occupancy', 'interest_share'].includes(detail.value?.payload?.kind))
const isDeferred = computed(() => detail.value?.payload?.kind === 'deferred_delivery')
const deferredAction = computed(() => isDeferred.value ? detail.value.payload.action : null)
const tabLabel = computed(() => ({ reminders: '提醒', waiting: '等待', system: '周期', deferred: '延期交付' }[tab.value]))
const detailTitle = computed(() => tab.value === 'waiting' ? '等待详情' : isDeferred.value || tab.value === 'deferred' ? '延期交付详情' : isSystem.value || tab.value === 'system' ? '调度周期详情' : '提醒详情')
const deferredPhases = { waiting: '等待重新入队', queued: '已入队，尚无尝试', attempted: '已有发送尝试', terminal: '此延期已结束，结果见回执' }
const editable = computed(() => detail.value && !isWork.value && !isSystem.value && !isDeferred.value && ['pending', 'claimed', 'processing', 'result_ready', 'review_required'].includes(detail.value.status))
const dirty = computed(() => editing.value && baseline.value && (draft.value.description.trim() !== baseline.value.saved.description || draftDue.value !== baseline.value.saved.due_at))
const { confirmLeave } = useUnsavedChanges(dirty)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const taskStates = [{ title: '全部状态', value: '' }, { title: '待触发', value: 'pending' }, { title: '已认领', value: 'claimed' }, { title: '处理中', value: 'processing' }, { title: '待处理结果', value: 'result_ready' }, { title: '待回执', value: 'awaiting_delivery' }, { title: '任务完成', value: 'completed' }, { title: '已取消', value: 'cancelled' }, { title: '失败', value: 'failed' }, { title: '送达未知', value: 'delivery_unknown' }, { title: '待核对', value: 'review_required' }, { title: 'Shadow／模拟观察', value: 'shadow_observed' }]
const waitingStates = [{ title: '全部状态', value: '' }, { title: '等待中', value: 'active' }, { title: '待核对', value: 'review_required' }, { title: '已结束', value: 'resolved' }, { title: '已过期', value: 'expired' }, { title: '已取消', value: 'cancelled' }]
const statusOptions = computed(() => tab.value === 'waiting' ? waitingStates : tab.value === 'system'
  ? taskStates.map(item => item.value === 'completed' ? { ...item, title: '本槽已结束' } : item) : taskStates)
const draftDue = computed(() => timeAnchor.value && draft.value.time === timeAnchor.value.text ? timeAnchor.value.value : parseTime(draft.value.time))
function clean(query) { return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '' && value !== undefined && value !== null)) }
function listQuery() { const { id: ignored, list_scene: originalScene, ...query } = route.query; if (originalScene !== undefined) query.scene = scalar(originalScene) || undefined; return query }
function open(item) { router.push({ name: 'tasks', query: { ...listQuery(), list_scene: scalar(route.query.scene), id: item.id, scene: item.scene_id, tab: tab.value } }) }
function close() { router.push({ name: 'tasks', query: listQuery() }) }
function changeTab(value) { router.push({ name: 'tasks', query: { return_to: route.query.return_to, scene: scalar(route.query.scene) || undefined, tab: value, status: value === 'waiting' ? 'active' : '', page: 1 } }) }
function applyFilters() { router.push({ name: 'tasks', query: { ...clean({ return_to: route.query.return_to, scene: filters.value.scene }), tab: tab.value, status: filters.value.status, page: 1 } }) }
function changePage(value) { router.push({ name: 'tasks', query: { ...route.query, page: value } }) }
function triggerDescription(item) { return item.wake_event_type ? '事件条件：' + item.wake_event_type : '按预定时间触发' }
function taskStatus(item) { return item.payload?.delivery_status === 'simulated' ? 'simulated' : item.status }
function inputTime(timestamp) {
  if (timestamp === null || timestamp === undefined) return ''
  const value = new Date(timestamp * 1000)
  if (!Number.isFinite(value.getTime()) || value.getFullYear()<1 || value.getFullYear()>9999) return ''
  return `${String(value.getFullYear()).padStart(4,'0')}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}T${String(value.getHours()).padStart(2,'0')}:${String(value.getMinutes()).padStart(2,'0')}:${String(value.getSeconds()).padStart(2,'0')}`
}
function parseTime(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text)
  if (!match) return NaN
  const parts = match.slice(1).map(value=>Number(value || 0)), value = new Date(text)
  const actual = [value.getFullYear(),value.getMonth()+1,value.getDate(),value.getHours(),value.getMinutes(),value.getSeconds()]
  return parts.every((part,index)=>part===actual[index]) ? value.getTime()/1000 : NaN
}
async function loadList() {
  const fresh = listGuard(), currentTab = tab.value
  loading.value = true; listError.value = ''
  const params = new URLSearchParams(clean({ scene_id: scalar(route.query.scene), status: routeStatus.value, kind: currentTab === 'waiting' ? undefined : currentTab === 'reminders' ? 'reminder' : currentTab, page: page.value, page_size: 30 }))
  try {
    const value = await api(`/api/cockpit/${currentTab === 'waiting' ? 'loops' : 'tasks'}?` + params)
    if (!fresh() || id.value) return
    rows.value = value.items; total.value = value.total; pageSize.value = value.page_size; readAt.value = Date.now() / 1000; loaded.value = true
  } catch (error) { if (fresh()) listError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function loadDetail({ reset = false, accept = () => true } = {}) {
  if (!id.value) return
  const own = detailGuard(), fresh=()=>own()&&accept(), current = id.value, currentTab = tab.value
  detailLoading.value = true; detailError.value = ''; detailMissing.value = false
  if (reset) { detail.value = null; detailReadAt.value = null }
  const scope = scalar(route.query.scene)
  try {
    const value = await api(`/api/cockpit/${currentTab === 'waiting' ? 'loops' : 'tasks'}/${encodeURIComponent(current)}` + (scope ? '?scene_id=' + encodeURIComponent(scope) : ''))
    if (!fresh()) return
    if (value.id !== current || (scope && value.scene_id !== scope)) throw new Error('详情未返回同一对象与场景，未采用。')
    detail.value = value; detailReadAt.value = Date.now() / 1000
    readbackPending.value = ''
    if (editing.value && baseline.value && hasConfigDraftChanges(baseline.value.saved,selectedFacts(value))) conflict.value = true
  } catch (error) { if (fresh()) { detailError.value = error.message; detailMissing.value = error.status === 404; if (detailMissing.value) detail.value = null } }
  finally { if (fresh()) detailLoading.value = false }
}
function startEdit() {
  if (!editable.value || saving.value || detailLoading.value || detailError.value || readbackPending.value) return
  baseline.value = { saved:selectedFacts(detail.value), time:inputTime(detail.value.due_at) }
  timeAnchor.value = {text:baseline.value.time,value:detail.value.due_at}
  draft.value = { description: baseline.value.saved.description, time: baseline.value.time }; editing.value = true; actionError.value = ''; feedback.value = ''; conflict.value = false
}
function cancelEdit() { if (!saving.value && confirmLeave()) { editing.value = false; baseline.value = null; conflict.value = false } }
function resolveConflict(keep) {
  if (saving.value || detailLoading.value || detailError.value || readbackPending.value || !editable.value || !baseline.value) return
  const current = selectedFacts(detail.value), before = baseline.value.saved
  if (keep && ['id','scene_id','created_at','source_event_id'].some(key=>current[key]!==before[key])) {
    actionError.value = '原提醒身份已改变，不能把旧草稿接到另一条提醒；请采用现值后重新编辑。'; return
  }
  if (!keep && !window.confirm('放弃此提醒尚未保存的修改，采用本次读取的事项和时间？')) return
  const timeChanged = draftDue.value !== before.due_at
  const nextDue = keep && timeChanged ? draftDue.value : current.due_at
  const next = keep ? {description:draft.value.description.trim()===before.description?current.description:draft.value.description,
    time:timeChanged?draft.value.time:inputTime(current.due_at)} : {description:current.description,time:inputTime(current.due_at)}
  baseline.value={saved:current,time:inputTime(current.due_at)};draft.value=next;conflict.value=false;actionError.value=''
  timeAnchor.value={text:next.time,value:nextDue}
  feedback.value=keep?'已保留实际编辑的事项／时间，未改字段采用现值。请核对触发条件后另行保存，尚未提交。':'已采用本次读取的保存值，没有修改提醒。'
}
function askAction(operation) {
  if (saving.value || detailLoading.value || detailError.value || readbackPending.value || !detail.value) return
  if (operation === 'update' && (!editable.value || !baseline.value || conflict.value || !draft.value.description.trim() || !Number.isFinite(draftDue.value))) return
  if (operation === 'cancel' && !editable.value || operation === 'trigger_now' && (!editable.value || detail.value.status!=='pending')
      || operation === 'resolve' && (tab.value!=='waiting' || !['active','review_required'].includes(detail.value.status))) return
  confirmation.value = { operation, id: detail.value.id, scene: detail.value.scene_id, tab: tab.value,
    baseline:operation==='update'?clone(baseline.value.saved):selectedFacts(detail.value,tab.value==='waiting'),
    description: operation === 'update' ? draft.value.description.trim() : tab.value === 'waiting' ? detail.value.intent : detail.value.description,
    ...(operation === 'update' ? { due_at: draftDue.value } : {}) }
  actionError.value = ''
}
const actionTitle = computed(() => ({ update: '确认修改提醒', trigger_now: '确认立即触发', cancel: '确认取消提醒', resolve: '确认结束等待' }[confirmation.value?.operation] || '确认操作'))
async function submitAction() {
  if (saving.value || detailLoading.value || detailError.value || readbackPending.value || !confirmation.value || !detail.value) return
  const value = confirmation.value
  if (hasConfigDraftChanges(value.baseline, selectedFacts(detail.value,value.tab==='waiting'))) {
    actionError.value='确认期间对象已变化，未提交旧确认。请核对当前状态后重新选择操作。'
    if (value.operation==='update')conflict.value=true
    confirmation.value=null;return
  }
  const fresh = actionGuard()
  saving.value = true; detailGuard(); detailLoading.value = false; actionError.value = ''; feedback.value = ''
  try {
    const result = await api(`/api/cockpit/${value.tab === 'waiting' ? 'loops' : 'tasks'}/${encodeURIComponent(value.id)}/${value.operation}`, {
      method: 'POST', ...(value.tab !== 'waiting' ? { body: JSON.stringify({baseline:value.baseline,
        ...(value.operation==='update'?{description:value.description,due_at:value.due_at}:{})}) } : {}),
    })
    if (!fresh()) return
    if (result.success !== true || (value.tab==='waiting'?result.loop_id:result.task_id) !== value.id) throw new Error('控制响应未确认同一对象；请沿原记录核对，未自动重复提交。')
    editing.value = false; baseline.value = null; confirmation.value = null; conflict.value = false; readbackPending.value=value.operation
    feedback.value = { update: '提醒修改已提交，请核对下方的预定时间。', trigger_now: '立即触发请求已提交，执行与送达仍以实际记录为准。', cancel: '提醒取消已提交，历史记录保留。', resolve: '人工结束等待已提交，来源消息保留。' }[value.operation]
    await loadDetail({accept:fresh})
  } catch (error) { if (fresh()) { actionError.value = error.message; confirmation.value = null; if (error.status === 409 && error.details != null) { if(value.operation==='update')conflict.value=true;await loadDetail({accept:fresh}) } } }
  finally { if (fresh()) saving.value = false }
}
function refresh() { if (saving.value) return; return id.value ? loadDetail() : loadList() }
function onVisible() { if (document.visibilityState === 'visible') refresh() }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => document.removeEventListener('visibilitychange', onVisible))
onBeforeRouteUpdate((to, from) => to.query.id !== from.query.id || to.query.scene !== from.query.scene || to.query.tab !== from.query.tab ? confirmLeave() : true)
watch(() => [route.query.id, route.query.scene, route.query.tab], () => {
  listGuard(); detailGuard(); actionGuard(); saving.value = false; editing.value = false; baseline.value = null; confirmation.value = null; actionError.value = ''; feedback.value = ''
  conflict.value=false; readbackPending.value=''; timeAnchor.value=null;detail.value=null;detailReadAt.value=null;detailLoading.value=false
  if (id.value) loadDetail({ reset: true })
}, { immediate: true, flush:'sync' })
watch(() => [route.query.id, route.query.scene, route.query.tab, route.query.status, route.query.page], () => {
  filters.value = { scene: scalar(route.query.scene), status: routeStatus.value }
  if (!id.value) { rows.value = []; total.value = 0; loaded.value = false; readAt.value = null; loadList() }
}, { immediate: true })
</script>

<template>
  <section class="tasks-page">
    <PageHeader :title="id ? detailTitle : '提醒、等待与周期'" :description="'提醒、等待回应、原行动延期与系统周期分别查看。时间采用浏览器时区：' + zone + '。'">
      <v-btn v-if="id" variant="text" :prepend-icon="mdiArrowLeft" @click="close">返回{{ tabLabel }}列表</v-btn><v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="id ? detailLoading : loading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <p v-if="id" class="auxiliary">切换对象或离页不撤销已经提交的控制。修改、立即触发和取消按原提醒核对；人工结束等待不代表对方已经回复。</p>
    <v-alert v-if="readbackPending" type="warning" variant="tonal" class="section-gap">控制请求已取得成功回执，但尚未读回当前对象。请先刷新核对，暂不再次提交；旧状态不等于操作失败。<v-btn variant="text" :disabled="saving" :loading="detailLoading" @click="refresh">重读当前对象</v-btn></v-alert>
    <v-tabs :model-value="tab" color="primary" class="section-gap" show-arrows @update:model-value="changeTab"><v-tab value="reminders">提醒</v-tab><v-tab value="waiting">等待回应</v-tab><v-tab value="deferred">延期交付</v-tab><v-tab value="system">心跳与兴趣分享</v-tab></v-tabs>
    <template v-if="!id">
      <v-card class="section-gap"><v-card-text><v-form class="task-filters" @submit.prevent="applyFilters"><ScopeSelect v-model="filters.scene" clearable /><v-select v-model="filters.status" :items="statusOptions" label="状态" hide-details /><v-btn type="submit" color="primary">筛选</v-btn></v-form><p v-if="tab === 'reminders'" class="work-guide">本列表只显示提醒。<RouterLink :to="{ name: 'jobs', query: clean({ return_to: route.query.return_to, scene: scalar(route.query.scene) }) }">查看信息工作</RouterLink></p></v-card-text></v-card>
      <v-alert v-if="listError" type="error" variant="tonal" title="列表读取失败" class="section-gap">{{ listError }}<div v-if="readAt">保留上次读取结果：{{ fmtTime(readAt) }}</div></v-alert><v-progress-linear v-if="loading" indeterminate class="section-gap" aria-label="正在读取列表" /><div v-if="loaded" class="list-meta"><span>共 {{ total }} 项 · 每页 {{ pageSize }} 项</span><span>读取于 {{ fmtTime(readAt) }}</span></div>
      <p v-if="tab === 'deferred'" class="work-guide section-gap">这里是已提交行动的延期，不是新提醒。等待释放、入队、发送尝试和真实回执分开保留；不能在此修改时间或重新触发。</p>
      <div class="task-list"><v-card v-for="item in rows" :key="item.id" tag="article" class="task-row"><div class="task-main"><RouterLink class="record-title two-lines" :to="{ name: 'tasks', query: { ...listQuery(), list_scene: scalar(route.query.scene), tab, id: item.id, scene: item.scene_id } }">{{ tab === 'waiting' ? item.intent : item.description }}</RouterLink><span v-if="tab === 'waiting'" class="target-name">等待对象：{{ item.target_actor_id }}</span><span v-if="tab === 'deferred'" class="target-name">{{ deferredPhases[item.payload.delivery_phase] || '旧记录未保存延期阶段' }} · 原定 {{ fmtTime(item.payload.original_due_at) }}</span><EntityLink v-if="tab === 'waiting'" type="event" :id="item.source_event_id" :scene-id="item.scene_id" label="来源消息" /><EntityLink type="scene" :id="item.scene_id" :scene-id="item.scene_id" /></div><div class="task-time"><template v-if="tab === 'waiting'"><span>开始 {{ fmtTime(item.created_at) }}</span><span>到期 {{ fmtTime(item.expires_at) }}</span></template><template v-else><span>{{ tab === 'deferred' ? '下次释放时间' : '预定' }} {{ fmtTime(item.due_at) }}</span><span class="auxiliary">{{ triggerDescription(item) }}</span></template></div><div><StatusBadge :domain="tab === 'waiting' ? 'waiting' : tab === 'system' ? 'schedule_slot' : 'task'" :status="tab === 'waiting' ? item.status : taskStatus(item)" /></div><v-btn variant="tonal" @click="open(item)">查看详情</v-btn></v-card></div>
      <v-card v-if="loaded && !rows.length && !listError" class="empty-state"><v-card-text>没有符合筛选条件的{{ tabLabel }}记录。</v-card-text></v-card><v-pagination v-if="pages > 1" :model-value="page" :length="pages" :total-visible="5" class="section-gap" @update:model-value="changePage" />
    </template>
    <template v-else>
      <v-alert v-if="detailError" :type="detailMissing ? 'warning' : 'error'" variant="tonal" :title="detailMissing ? '对象不存在或不属于此场景' : '详情读取失败'" class="section-gap">{{ detailError }}<div v-if="detailReadAt">上次读取：{{ fmtTime(detailReadAt) }}</div></v-alert><v-skeleton-loader v-if="detailLoading && !detail" type="article" class="section-gap" />
      <v-alert v-if="feedback" type="success" variant="tonal" class="section-gap" role="status">{{ feedback }}</v-alert><v-alert v-if="actionError" type="error" variant="tonal" class="section-gap" role="alert">{{ actionError }}</v-alert>
      <v-card v-if="editing&&!detail" class="section-gap"><v-card-text><p>当前提醒不可读取，原草稿保留，未套用到其他提醒。</p><ResourceViewer title="未保存的提醒修改" :content="draft" /><v-btn variant="text" :disabled="saving" @click="cancelEdit">放弃提醒草稿</v-btn></v-card-text></v-card>
      <template v-if="detail">
        <v-card class="section-gap"><v-card-text><h2 class="full-copy">{{ tab === 'waiting' ? detail.intent : detail.description }}</h2><div class="identity-line"><span class="object-id">{{ detail.id }}</span><EntityLink type="scene" :id="detail.scene_id" :scene-id="detail.scene_id" /><StatusBadge :domain="tab === 'waiting' ? 'waiting' : isWork ? 'job_delivery' : isSystem ? 'schedule_slot' : 'task'" :status="isWork && detail.delivery_required === false ? 'not_required' : tab === 'waiting' || isWork ? detail.status : taskStatus(detail)" /></div><p class="auxiliary">读取于 {{ fmtTime(detailReadAt) }}。任务状态不能代替平台送达证据，具体结果沿原回执核对。</p>
          <template v-if="tab === 'waiting'"><dl class="fact-list"><div><dt>等待对象</dt><dd>{{ detail.target_actor_id }}</dd></div><div><dt>开始时间</dt><dd>{{ fmtTime(detail.created_at) }}</dd></div><div><dt>到期时间</dt><dd>{{ fmtTime(detail.expires_at) }}</dd></div></dl><v-alert v-if="detail.status==='review_required'" type="warning" variant="tonal">该挂起请求跨越了进程重启，保留原来源与预算供人工核对，不自动重发。</v-alert><ResourceViewer v-if="detail.resume_state" title="挂起请求与原预算" :content="detail.resume_state" /><h3 class="section-title">来源消息</h3><EntityLink type="event" :id="detail.source_event_id" :scene-id="detail.scene_id" /><div class="action-row"><v-btn color="error" variant="outlined" :disabled="saving || detailLoading || !!detailError || !!readbackPending || !['active','review_required'].includes(detail.status)" @click="askAction('resolve')">人工结束等待</v-btn></div></template>
          <template v-else-if="isSystem"><p class="mt-4">{{ detail.payload.kind }} · 周期 {{ detail.payload.slot ?? '未记录' }} · 预定 {{ fmtTime(detail.due_at) }}</p><p>此记录由调度配置管理。周期完成仅表示本轮处理结束，研究结果和发送回执分别核对。</p><EntityLink v-if="detail.payload.job_id" type="job" :id="detail.payload.job_id" :scene-id="detail.scene_id" label="关联研究工作" /><EntityLink v-if="detail.trigger_event_id" type="event" :id="detail.trigger_event_id" :scene-id="detail.scene_id" label="实际触发事件" /><ResourceViewer title="周期结果与跳过原因" :content="detail.payload" /><ResourceViewer title="最近调度观察（最多 30 条）" :content="detail.scheduler_observations" /></template>
          <template v-else-if="isDeferred">
            <v-alert type="info" variant="tonal" class="mt-4">原行动延期交付，只读查看；这不是新的提醒，不提供改时间、立即触发或重发按钮。控制原工作或提醒不会抹掉已有发送尝试。</v-alert>
            <dl class="fact-list"><div><dt>原定时间</dt><dd>{{ fmtTime(detail.payload.original_due_at) }}</dd></div><div><dt>记录的下次释放时间</dt><dd>{{ fmtTime(detail.due_at) }}</dd></div><div><dt>延期阶段</dt><dd>{{ deferredPhases[detail.payload.delivery_phase] || '旧记录未保存' }}</dd></div></dl>
            <p class="breakable">原行动 {{ detail.payload.action_id }}</p><p class="deferred-reason">{{ detail.payload.detail || '未保存延期原因' }}</p>
            <div v-if="deferredAction" class="link-list mt-4"><p>原请求者 QQ {{ deferredAction.requester_qq_uid || '未记录人类请求者' }} · 原表达模式 {{ deferredAction.origin_mode || '未记录' }}</p><p>原回应对象：{{ deferredAction.response_actor_ids?.join('、') || '未记录' }}</p><EntityLink v-if="deferredAction.origin_event_id" type="event" :id="deferredAction.origin_event_id" :scene-id="detail.scene_id" label="原请求或触发事件" /><EntityLink v-if="deferredAction.job_id" type="job" :id="deferredAction.job_id" :scene-id="detail.scene_id" :label="`回到原工作（行动依据 v${deferredAction.job_revision ?? '未记录'}）`" /><EntityLink v-else-if="deferredAction.fulfils_task_id" type="task" :id="deferredAction.fulfils_task_id" :scene-id="detail.scene_id" label="回到原提醒" /><EntityLink v-if="deferredAction.file_asset_id" type="file" :id="deferredAction.file_asset_id" :job-id="deferredAction.job_id" :scene-id="detail.scene_id" label="查看文件资产与上传回执" /></div>
            <div class="identity-line"><span>保存的交付结局</span><StatusBadge v-if="detail.payload.delivery_status" domain="delivery" :status="detail.payload.delivery_status" /><span v-else class="auxiliary">尚无终态字段，不等于尚未尝试</span></div>
            <p class="auxiliary">attempted 且无可靠回执时保留未知；已过期的普通话题不会因醒来自动补发，已有工作成果也不重新计算。</p>
          </template>
          <template v-else-if="isWork"><v-alert type="info" variant="tonal" class="mt-4">这是信息工作对应的底层任务记录。工作要求、版本、资料和合法恢复在工作详情中管理。<div class="mt-3"><EntityLink type="job" :id="detail.id" :scene-id="detail.scene_id" label="打开对应工作" /></div></v-alert></template>
          <template v-else><dl class="fact-list"><div><dt>预定时间</dt><dd>{{ fmtTime(detail.due_at) }}</dd></div><div><dt>触发方式</dt><dd>{{ triggerDescription(detail) }}</dd></div><div><dt>创建时间</dt><dd>{{ fmtTime(detail.created_at) }}</dd></div></dl><div class="action-row"><v-btn variant="outlined" :prepend-icon="mdiPencilOutline" :disabled="!editable || saving || editing || detailLoading || !!detailError || !!readbackPending" @click="startEdit">修改提醒</v-btn><v-btn variant="outlined" :prepend-icon="mdiClockFast" :disabled="detail.status !== 'pending' || saving || editing || detailLoading || !!detailError || !!readbackPending" @click="askAction('trigger_now')">立即触发</v-btn><v-btn color="error" variant="outlined" :prepend-icon="mdiCancel" :disabled="!editable || saving || editing || detailLoading || !!detailError || !!readbackPending" @click="askAction('cancel')">取消提醒</v-btn></div></template>
        </v-card-text></v-card>
        <v-card v-if="editing" class="section-gap edit-card">
          <v-card-title>修改当前提醒</v-card-title>
          <v-card-text>
            <v-alert v-if="conflict" type="warning" variant="tonal" class="mb-4">
              <p>提醒已有变化或本次修改被拒绝，草稿保留。当前展示为 {{ fmtTime(detailReadAt) }} 的读取，不自动替换编辑基线。</p>
              <p v-if="detailError" class="mt-2">当前重读失败，先刷新成功再选择，不能使用旧样本继续提交。</p>
              <ResourceViewer title="本次读取的提醒与触发条件" :content="selectedFacts(detail)" class="mt-3" />
              <p class="mt-3">保留只重建自己改过的事项／时间，其他值采用现值。操作仍须另行确认保存，原事务会再次核对。</p>
              <div class="action-row"><v-btn variant="outlined" :disabled="!editable||saving||detailLoading||!!detailError||!!readbackPending" @click="resolveConflict(true)">保留实际改动，采用新基线</v-btn><v-btn variant="text" :disabled="!editable||saving||detailLoading||!!detailError||!!readbackPending" @click="resolveConflict(false)">放弃草稿，采用现值</v-btn></div>
            </v-alert>
            <v-textarea v-model="draft.description" label="提醒事项" rows="3" auto-grow :disabled="saving||!!readbackPending" />
            <v-text-field v-model="draft.time" type="datetime-local" step="1" :label="'执行时间（' + zone + '）'" :disabled="saving||!!readbackPending" />
            <p v-if="!baseline.time" class="auxiliary">原绝对时间无法在日期控件表示；未编辑时保留原值，不按留空清除。</p>
            <p v-if="Number.isFinite(draftDue)" class="time-confirm">对应绝对时间：{{ absoluteTime(draftDue) }}</p><p v-else role="alert" class="text-error">请填写有效的本地日期与时间；无效日期或夏令时跳过的时间不会自动改成另一个时刻。</p>
            <div class="action-row"><v-btn color="primary" :disabled="!editable || !dirty || !draft.description.trim() || !Number.isFinite(draftDue) || conflict || saving || detailLoading || !!detailError || !!readbackPending" @click="askAction('update')">保存修改</v-btn><v-btn variant="text" :disabled="saving" @click="cancelEdit">取消编辑</v-btn></div>
          </v-card-text>
        </v-card>
        <v-card v-if="tab !== 'waiting'" class="section-gap"><v-card-text class="detail-body"><ResourceViewer v-if="detail.payload.result !== undefined" title="完整结果" :content="detail.payload.result" /><v-alert v-if="detail.payload.error" type="warning" variant="tonal" class="mt-4">{{ detail.payload.error }}</v-alert><h3 class="section-title">原始来源</h3><div class="link-list"><EntityLink v-for="eventId in detail.payload.source_event_ids || []" :key="eventId" type="event" :id="eventId" :scene-id="detail.scene_id" /><EntityLink v-if="detail.payload.delivery_event_id" type="event" :id="detail.payload.delivery_event_id" :scene-id="detail.scene_id" label="查看发送回执" /></div><v-expansion-panels variant="accordion" class="mt-4"><v-expansion-panel title="触发条件与完整任务记录"><v-expansion-panel-text><ResourceViewer title="触发匹配条件" :content="detail.wake_match" /><ResourceViewer title="完整任务记录" :content="detail" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></v-card-text></v-card>
      </template>
    </template>
    <v-dialog :model-value="Boolean(confirmation)" :persistent="saving" max-width="620" @update:model-value="value => { if (!value && !saving) confirmation = null }"><v-card><v-card-title class="dialog-title">{{ actionTitle }}</v-card-title><v-card-text v-if="confirmation"><p class="full-copy">{{ confirmation.description }}</p><p class="auxiliary">对象 {{ confirmation.id }} · {{ confirmation.scene }} · 原状态 {{ confirmation.baseline.status }}</p><details class="my-3"><summary>核对本次选择的原值</summary><ResourceViewer title="确认时选择的对象与触发条件" :content="confirmation.baseline" /></details><p v-if="confirmation.operation === 'update'">保存时间：{{ fmtTime(confirmation.due_at) }}（{{ zone }}）<span class="time-confirm">{{ absoluteTime(confirmation.due_at) }}</span></p><p v-else-if="confirmation.operation === 'trigger_now'">将此提醒安排为立即触发，随后仍按既有判断与发送配置处理；本按钮不直接宣布履约。</p><p v-else-if="confirmation.operation === 'cancel'">取消此提醒，保留历史记录与已有结果。</p><p v-else>手动结束当前等待，不代表对方已经回复。</p></v-card-text><v-card-actions class="dialog-actions"><v-btn variant="text" :disabled="saving" @click="confirmation = null">返回核对</v-btn><v-btn :color="['cancel', 'resolve'].includes(confirmation?.operation) ? 'error' : 'primary'" :loading="saving" :disabled="saving || detailLoading || !!detailError || !!readbackPending" @click="submitAction">确认{{ confirmation?.operation === 'update' ? '保存' : confirmation?.operation === 'trigger_now' ? '触发' : confirmation?.operation === 'cancel' ? '取消' : '结束' }}</v-btn></v-card-actions></v-card></v-dialog>
  </section>
</template>

<style scoped>
.breakable,.deferred-reason{overflow-wrap:anywhere}.deferred-reason{white-space:pre-wrap;line-height:1.7;margin-top:12px}
.tasks-page{min-width:0}.tasks-page>.page-header{margin-bottom:24px}.tasks-page>.page-header+.v-tabs{margin-top:0}.section-gap{margin-top:20px}.task-filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:center}.work-guide{margin:8px 0 0;font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.list-meta{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;margin:16px 0;font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.task-list{display:grid;gap:12px}.task-row{padding:18px;display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,220px) minmax(80px,110px) auto;gap:20px;align-items:center}.task-main{display:grid;gap:8px;min-width:0}.record-title{font-size:15px;font-weight:600;line-height:1.6;color:rgb(var(--v-theme-primary));text-decoration:none}.record-title:hover{text-decoration:underline}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.task-time{display:grid;gap:8px;font-size:13px;line-height:1.5}.target-name{font-size:13px;overflow-wrap:anywhere}.auxiliary{color:rgb(var(--v-theme-on-surface-variant));font-size:13px;line-height:1.55}.full-copy{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;font-size:19px;font-weight:600}.identity-line,.action-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}.object-id{overflow-wrap:anywhere;font-size:13px}.fact-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:24px 0}.fact-list dt{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));margin-bottom:6px}.fact-list dd{margin:0;line-height:1.6;overflow-wrap:anywhere}.section-title{font-size:17px;margin:20px 0 12px}.link-list{display:grid;gap:10px}.edit-card{max-width:900px}.time-confirm{display:block;font-size:13px;color:rgb(var(--v-theme-on-surface-variant));overflow-wrap:anywhere;margin-top:8px}.dialog-title{white-space:normal}.dialog-actions{padding:16px;flex-wrap:wrap}.empty-state{padding:20px;text-align:center}.detail-body{min-width:0}@media(max-width:1150px){.task-row{grid-template-columns:minmax(0,1fr) auto;gap:16px}.task-time{grid-column:1;grid-row:2}.task-row>div:nth-child(3){grid-column:1;grid-row:3}.task-row>.v-btn{grid-column:2;grid-row:1/4}}@media(max-width:650px){.task-filters,.fact-list{grid-template-columns:1fr}.task-row{grid-template-columns:1fr;padding:16px}.task-time,.task-row>div:nth-child(3),.task-row>.v-btn{grid-column:auto;grid-row:auto}.task-row>.v-btn{justify-self:start}.identity-line{align-items:flex-start;flex-direction:column}.action-row>.v-btn{flex-grow:1}.full-copy{font-size:18px}}
</style>
