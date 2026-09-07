<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { mdiArrowLeft, mdiRefresh, mdiPencilOutline, mdiClockFast, mdiCancel } from '@mdi/js'
import { api } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const tab = computed(() => route.query.tab === 'waiting' ? 'waiting' : 'reminders')
const id = computed(() => scalar(route.query.id))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const routeStatus = computed(() => route.query.status === undefined ? (tab.value === 'waiting' ? 'active' : '') : scalar(route.query.status))
const filters = ref({ scene: '', status: '' })
const rows = ref([]), total = ref(0), pageSize = ref(30), loading = ref(false), listError = ref(''), loaded = ref(false), readAt = ref(null)
const detail = ref(null), detailLoading = ref(false), detailError = ref(''), detailMissing = ref(false), detailReadAt = ref(null)
const editing = ref(false), draft = ref({ description: '', time: '' }), baseline = ref(null), confirmation = ref(null)
const saving = ref(false), actionError = ref(''), feedback = ref('')
let listRequest = 0, detailRequest = 0
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
function fmtTime(timestamp) { return timestamp === null || timestamp === undefined ? '—' : new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) }
const isWork = computed(() => tab.value === 'reminders' && detail.value?.payload.kind === 'agent_job')
const editable = computed(() => detail.value && !isWork.value && ['pending', 'claimed', 'processing', 'result_ready', 'review_required'].includes(detail.value.status))
const dirty = computed(() => editing.value && baseline.value && (draft.value.description !== baseline.value.description || draft.value.time !== baseline.value.time))
const { confirmLeave } = useUnsavedChanges(dirty)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const taskStates = [{ title: '全部状态', value: '' }, { title: '待触发', value: 'pending' }, { title: '处理中', value: 'processing' }, { title: '待处理结果', value: 'result_ready' }, { title: '待回执', value: 'awaiting_delivery' }, { title: '已兑现', value: 'completed' }, { title: '已取消', value: 'cancelled' }, { title: '失败', value: 'failed' }, { title: '送达未知', value: 'delivery_unknown' }, { title: '待核对', value: 'review_required' }, { title: 'Shadow', value: 'shadow_observed' }]
const waitingStates = [{ title: '全部状态', value: '' }, { title: '等待中', value: 'active' }, { title: '已结束', value: 'resolved' }, { title: '已过期', value: 'expired' }, { title: '已取消', value: 'cancelled' }]
const statusOptions = computed(() => tab.value === 'waiting' ? waitingStates : taskStates)
const draftDue = computed(() => baseline.value && draft.value.time === baseline.value.time ? baseline.value.due_at : new Date(draft.value.time).getTime() / 1000)
function clean(query) { return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== '' && value !== undefined && value !== null)) }
function listQuery() { const { id: ignored, list_scene: originalScene, ...query } = route.query; if (originalScene !== undefined) query.scene = scalar(originalScene) || undefined; return query }
function open(item) { router.push({ name: 'tasks', query: { ...listQuery(), list_scene: scalar(route.query.scene), id: item.id, scene: item.scene_id, tab: tab.value } }) }
function close() { router.push({ name: 'tasks', query: listQuery() }) }
function changeTab(value) { router.push({ name: 'tasks', query: { scene: scalar(route.query.scene) || undefined, tab: value, status: value === 'waiting' ? 'active' : '', page: 1 } }) }
function applyFilters() { router.push({ name: 'tasks', query: { ...clean({ scene: filters.value.scene }), tab: tab.value, status: filters.value.status, page: 1 } }) }
function changePage(value) { router.push({ name: 'tasks', query: { ...route.query, page: value } }) }
function triggerDescription(item) { return item.wake_event_type ? '事件条件：' + item.wake_event_type : '按预定时间触发' }
function inputTime(timestamp) { if (timestamp === null || timestamp === undefined) return ''; const value = new Date(timestamp * 1000); return new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 19) }
async function loadList() {
  const request = ++listRequest, currentTab = tab.value
  loading.value = true; listError.value = ''
  const params = new URLSearchParams(clean({ scene_id: scalar(route.query.scene), status: routeStatus.value, kind: currentTab === 'reminders' ? 'reminder' : undefined, page: page.value, page_size: 30 }))
  try {
    const value = await api(`/api/cockpit/${currentTab === 'waiting' ? 'loops' : 'tasks'}?` + params)
    if (request !== listRequest || id.value || currentTab !== tab.value) return
    rows.value = value.items; total.value = value.total; pageSize.value = value.page_size; readAt.value = Date.now() / 1000; loaded.value = true
  } catch (error) { if (request === listRequest) listError.value = error.message }
  finally { if (request === listRequest) loading.value = false }
}
async function loadDetail({ reset = false } = {}) {
  if (!id.value) return
  const request = ++detailRequest, current = id.value, currentTab = tab.value
  detailLoading.value = true; detailError.value = ''; detailMissing.value = false
  if (reset) { detail.value = null; detailReadAt.value = null }
  const scope = scalar(route.query.scene)
  try {
    const value = await api(`/api/cockpit/${currentTab === 'waiting' ? 'loops' : 'tasks'}/${encodeURIComponent(current)}` + (scope ? '?scene_id=' + encodeURIComponent(scope) : ''))
    if (request !== detailRequest || id.value !== current || tab.value !== currentTab) return
    detail.value = value; detailReadAt.value = Date.now() / 1000
  } catch (error) { if (request === detailRequest) { detailError.value = error.message; detailMissing.value = error.status === 404; if (detailMissing.value) detail.value = null } }
  finally { if (request === detailRequest) detailLoading.value = false }
}
function startEdit() {
  if (!editable.value || saving.value) return
  baseline.value = { id: detail.value.id, description: detail.value.description, due_at: detail.value.due_at, time: inputTime(detail.value.due_at) }
  draft.value = { description: baseline.value.description, time: baseline.value.time }; editing.value = true; actionError.value = ''; feedback.value = ''
}
function cancelEdit() { if (confirmLeave()) { editing.value = false; baseline.value = null } }
function askAction(operation) {
  if (saving.value || !detail.value) return
  if (operation === 'update' && (!editable.value || !draft.value.description.trim() || !Number.isFinite(draftDue.value))) return
  confirmation.value = { operation, id: detail.value.id, scene: detail.value.scene_id, tab: tab.value,
    description: operation === 'update' ? draft.value.description.trim() : tab.value === 'waiting' ? detail.value.intent : detail.value.description,
    ...(operation === 'update' ? { due_at: draftDue.value } : {}) }
  actionError.value = ''
}
const actionTitle = computed(() => ({ update: '确认修改提醒', trigger_now: '确认立即触发', cancel: '确认取消提醒', resolve: '确认结束等待' }[confirmation.value?.operation] || '确认操作'))
async function submitAction() {
  if (saving.value || !confirmation.value) return
  const value = confirmation.value, scope = scalar(route.query.scene)
  saving.value = true; ++detailRequest; detailLoading.value = false; actionError.value = ''; feedback.value = ''
  try {
    await api(`/api/cockpit/${value.tab === 'waiting' ? 'loops' : 'tasks'}/${encodeURIComponent(value.id)}/${value.operation}`, {
      method: 'POST', ...(value.operation === 'update' ? { body: JSON.stringify({ description: value.description, due_at: value.due_at }) } : {}),
    })
    if (id.value !== value.id || tab.value !== value.tab || scalar(route.query.scene) !== scope) return
    editing.value = false; baseline.value = null; confirmation.value = null
    feedback.value = { update: '提醒修改已提交，请核对下方的预定时间。', trigger_now: '立即触发请求已提交，执行与送达仍以实际记录为准。', cancel: '提醒取消已提交，历史记录保留。', resolve: '人工结束等待已提交，来源消息保留。' }[value.operation]
    await loadDetail()
  } catch (error) { if (id.value === value.id && tab.value === value.tab && scalar(route.query.scene) === scope) { actionError.value = error.message; confirmation.value = null; if (error.status === 409) await loadDetail() } }
  finally { saving.value = false }
}
function refresh() { if (saving.value) return; return id.value ? loadDetail() : loadList() }
function onVisible() { if (document.visibilityState === 'visible') refresh() }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => { ++listRequest; ++detailRequest; document.removeEventListener('visibilitychange', onVisible) })
onBeforeRouteUpdate((to, from) => to.query.id !== from.query.id || to.query.scene !== from.query.scene || to.query.tab !== from.query.tab ? confirmLeave() : true)
watch(() => [route.query.id, route.query.scene, route.query.tab], () => {
  ++listRequest; ++detailRequest; editing.value = false; baseline.value = null; confirmation.value = null; actionError.value = ''; feedback.value = ''
  if (id.value) loadDetail({ reset: true })
}, { immediate: true })
watch(() => [route.query.id, route.query.scene, route.query.tab, route.query.status, route.query.page], () => {
  filters.value = { scene: scalar(route.query.scene), status: routeStatus.value }
  if (!id.value) { rows.value = []; total.value = 0; loaded.value = false; readAt.value = null; loadList() }
}, { immediate: true })
</script>

<template>
  <section class="tasks-page">
    <PageHeader :title="id ? (tab === 'waiting' ? '等待详情' : '提醒详情') : '提醒与等待'" :description="'管理明确的提醒与等待。时间采用浏览器时区：' + zone + '。'">
      <v-btn v-if="id" variant="text" :prepend-icon="mdiArrowLeft" @click="close">返回{{ tab === 'waiting' ? '等待' : '提醒' }}列表</v-btn><v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="id ? detailLoading : loading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <v-tabs :model-value="tab" color="primary" class="section-gap" @update:model-value="changeTab"><v-tab value="reminders">提醒</v-tab><v-tab value="waiting">等待回应</v-tab></v-tabs>
    <template v-if="!id">
      <v-card class="section-gap"><v-card-text><v-form class="task-filters" @submit.prevent="applyFilters"><ScopeSelect v-model="filters.scene" clearable /><v-select v-model="filters.status" :items="statusOptions" label="状态" hide-details /><v-btn type="submit" color="primary">筛选</v-btn></v-form><p v-if="tab === 'reminders'" class="work-guide">本列表只显示提醒。<RouterLink :to="{ name: 'jobs', query: clean({ scene: scalar(route.query.scene) }) }">查看信息工作</RouterLink></p></v-card-text></v-card>
      <v-alert v-if="listError" type="error" variant="tonal" title="列表读取失败" class="section-gap">{{ listError }}<div v-if="readAt">保留上次读取结果：{{ fmtTime(readAt) }}</div></v-alert><v-progress-linear v-if="loading" indeterminate class="section-gap" aria-label="正在读取列表" /><div v-if="loaded" class="list-meta"><span>共 {{ total }} 项 · 每页 {{ pageSize }} 项</span><span>读取于 {{ fmtTime(readAt) }}</span></div>
      <div class="task-list"><v-card v-for="item in rows" :key="item.id" tag="article" class="task-row"><div class="task-main"><RouterLink class="record-title two-lines" :to="{ name: 'tasks', query: { ...listQuery(), list_scene: scalar(route.query.scene), tab, id: item.id, scene: item.scene_id } }">{{ tab === 'waiting' ? item.intent : item.description }}</RouterLink><span v-if="tab === 'waiting'" class="target-name">等待对象：{{ item.target_actor_id }}</span><EntityLink v-if="tab === 'waiting'" type="event" :id="item.source_event_id" :scene-id="item.scene_id" label="来源消息" /><EntityLink type="scene" :id="item.scene_id" :scene-id="item.scene_id" /></div><div class="task-time"><template v-if="tab === 'waiting'"><span>开始 {{ fmtTime(item.created_at) }}</span><span>到期 {{ fmtTime(item.expires_at) }}</span></template><template v-else><span>预定 {{ fmtTime(item.due_at) }}</span><span class="auxiliary">{{ triggerDescription(item) }}</span></template></div><div><StatusBadge :domain="tab === 'waiting' ? 'waiting' : 'task'" :status="item.status" /></div><v-btn variant="tonal" @click="open(item)">查看详情</v-btn></v-card></div>
      <v-card v-if="loaded && !rows.length && !listError" class="empty-state"><v-card-text>没有符合筛选条件的{{ tab === 'waiting' ? '等待记录' : '提醒' }}。</v-card-text></v-card><v-pagination v-if="pages > 1" :model-value="page" :length="pages" :total-visible="5" class="section-gap" @update:model-value="changePage" />
    </template>
    <template v-else>
      <v-alert v-if="detailError" :type="detailMissing ? 'warning' : 'error'" variant="tonal" :title="detailMissing ? '对象不存在或不属于此场景' : '详情读取失败'" class="section-gap">{{ detailError }}<div v-if="detailReadAt">上次读取：{{ fmtTime(detailReadAt) }}</div></v-alert><v-skeleton-loader v-if="detailLoading && !detail" type="article" class="section-gap" />
      <template v-if="detail">
        <v-card class="section-gap"><v-card-text><h2 class="full-copy">{{ tab === 'waiting' ? detail.intent : detail.description }}</h2><div class="identity-line"><span class="object-id">{{ detail.id }}</span><EntityLink type="scene" :id="detail.scene_id" :scene-id="detail.scene_id" /><StatusBadge :domain="tab === 'waiting' ? 'waiting' : isWork ? 'job_delivery' : 'task'" :status="detail.status" /></div><p class="auxiliary">读取于 {{ fmtTime(detailReadAt) }}</p>
          <template v-if="tab === 'waiting'"><dl class="fact-list"><div><dt>等待对象</dt><dd>{{ detail.target_actor_id }}</dd></div><div><dt>开始时间</dt><dd>{{ fmtTime(detail.created_at) }}</dd></div><div><dt>到期时间</dt><dd>{{ fmtTime(detail.expires_at) }}</dd></div></dl><h3 class="section-title">来源消息</h3><EntityLink type="event" :id="detail.source_event_id" :scene-id="detail.scene_id" /><div class="action-row"><v-btn color="error" variant="outlined" :disabled="saving || detail.status !== 'active'" @click="askAction('resolve')">人工结束等待</v-btn></div></template>
          <template v-else-if="isWork"><v-alert type="info" variant="tonal" class="mt-4">这是信息工作对应的底层任务记录。工作要求、版本、资料和合法恢复在工作详情中管理。<div class="mt-3"><EntityLink type="job" :id="detail.id" :scene-id="detail.scene_id" label="打开对应工作" /></div></v-alert></template>
          <template v-else><dl class="fact-list"><div><dt>预定时间</dt><dd>{{ fmtTime(detail.due_at) }}</dd></div><div><dt>触发方式</dt><dd>{{ triggerDescription(detail) }}</dd></div><div><dt>创建时间</dt><dd>{{ fmtTime(detail.created_at) }}</dd></div></dl><div class="action-row"><v-btn variant="outlined" :prepend-icon="mdiPencilOutline" :disabled="!editable || saving || editing" @click="startEdit">修改提醒</v-btn><v-btn variant="outlined" :prepend-icon="mdiClockFast" :disabled="detail.status !== 'pending' || saving || editing" @click="askAction('trigger_now')">立即触发</v-btn><v-btn color="error" variant="outlined" :prepend-icon="mdiCancel" :disabled="!editable || saving || editing" @click="askAction('cancel')">取消提醒</v-btn></div></template>
        </v-card-text></v-card>
        <v-alert v-if="feedback" type="success" variant="tonal" class="section-gap" role="status">{{ feedback }}</v-alert><v-alert v-if="actionError" type="error" variant="tonal" class="section-gap" role="alert">{{ actionError }}</v-alert>
        <v-card v-if="editing" class="section-gap edit-card"><v-card-title>修改当前提醒</v-card-title><v-card-text><v-textarea v-model="draft.description" label="提醒事项" rows="3" auto-grow :disabled="saving" /><v-text-field v-model="draft.time" type="datetime-local" step="1" :label="'执行时间（' + zone + '）'" :disabled="saving" /><p v-if="Number.isFinite(draftDue)" class="time-confirm">对应绝对时间：{{ new Date(draftDue * 1000).toISOString() }}</p><div class="action-row"><v-btn color="primary" :disabled="!editable || !dirty || !draft.description.trim() || !Number.isFinite(draftDue) || saving" @click="askAction('update')">保存修改</v-btn><v-btn variant="text" :disabled="saving" @click="cancelEdit">取消编辑</v-btn></div></v-card-text></v-card>
        <v-card v-if="tab === 'reminders'" class="section-gap"><v-card-text class="detail-body"><ResourceViewer v-if="detail.payload.result !== undefined" title="完整结果" :content="detail.payload.result" /><v-alert v-if="detail.payload.error" type="warning" variant="tonal" class="mt-4">{{ detail.payload.error }}</v-alert><h3 class="section-title">原始来源</h3><div class="link-list"><EntityLink v-for="eventId in detail.payload.source_event_ids || []" :key="eventId" type="event" :id="eventId" :scene-id="detail.scene_id" /><EntityLink v-if="detail.payload.delivery_event_id" type="event" :id="detail.payload.delivery_event_id" :scene-id="detail.scene_id" label="查看发送回执" /></div><v-expansion-panels variant="accordion" class="mt-4"><v-expansion-panel title="触发条件与完整任务记录"><v-expansion-panel-text><ResourceViewer title="触发匹配条件" :content="detail.wake_match" /><ResourceViewer title="完整任务记录" :content="detail" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></v-card-text></v-card>
      </template>
    </template>
    <v-dialog :model-value="Boolean(confirmation)" :persistent="saving" max-width="620" @update:model-value="value => { if (!value && !saving) confirmation = null }"><v-card><v-card-title class="dialog-title">{{ actionTitle }}</v-card-title><v-card-text v-if="confirmation"><p class="full-copy">{{ confirmation.description }}</p><p v-if="confirmation.operation === 'update'">保存时间：{{ fmtTime(confirmation.due_at) }}（{{ zone }}）<span class="time-confirm">{{ new Date(confirmation.due_at * 1000).toISOString() }}</span></p><p v-else-if="confirmation.operation === 'trigger_now'">将此提醒安排为立即触发，随后仍按既有判断与发送配置处理；本按钮不直接宣布履约。</p><p v-else-if="confirmation.operation === 'cancel'">取消此提醒，保留历史记录与已有结果。</p><p v-else>手动结束当前等待，不代表对方已经回复。</p></v-card-text><v-card-actions class="dialog-actions"><v-btn variant="text" :disabled="saving" @click="confirmation = null">返回核对</v-btn><v-btn :color="['cancel', 'resolve'].includes(confirmation?.operation) ? 'error' : 'primary'" :loading="saving" :disabled="saving" @click="submitAction">确认{{ confirmation?.operation === 'update' ? '保存' : confirmation?.operation === 'trigger_now' ? '触发' : confirmation?.operation === 'cancel' ? '取消' : '结束' }}</v-btn></v-card-actions></v-card></v-dialog>
  </section>
</template>

<style scoped>
.tasks-page{min-width:0}.tasks-page>.page-header{margin-bottom:24px}.tasks-page>.page-header+.v-tabs{margin-top:0}.section-gap{margin-top:20px}.task-filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:center}.work-guide{margin:8px 0 0;font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.list-meta{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;margin:16px 0;font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.task-list{display:grid;gap:12px}.task-row{padding:18px;display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,220px) minmax(80px,110px) auto;gap:20px;align-items:center}.task-main{display:grid;gap:8px;min-width:0}.record-title{font-size:15px;font-weight:600;line-height:1.6;color:rgb(var(--v-theme-primary));text-decoration:none}.record-title:hover{text-decoration:underline}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.task-time{display:grid;gap:8px;font-size:13px;line-height:1.5}.target-name{font-size:13px;overflow-wrap:anywhere}.auxiliary{color:rgb(var(--v-theme-on-surface-variant));font-size:13px;line-height:1.55}.full-copy{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;font-size:19px;font-weight:600}.identity-line,.action-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:14px 0}.object-id{overflow-wrap:anywhere;font-size:13px}.fact-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px;margin:24px 0}.fact-list dt{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));margin-bottom:6px}.fact-list dd{margin:0;line-height:1.6;overflow-wrap:anywhere}.section-title{font-size:17px;margin:20px 0 12px}.link-list{display:grid;gap:10px}.edit-card{max-width:900px}.time-confirm{display:block;font-size:13px;color:rgb(var(--v-theme-on-surface-variant));overflow-wrap:anywhere;margin-top:8px}.dialog-title{white-space:normal}.dialog-actions{padding:16px;flex-wrap:wrap}.empty-state{padding:20px;text-align:center}.detail-body{min-width:0}@media(max-width:1150px){.task-row{grid-template-columns:minmax(0,1fr) auto;gap:16px}.task-time{grid-column:1;grid-row:2}.task-row>div:nth-child(3){grid-column:1;grid-row:3}.task-row>.v-btn{grid-column:2;grid-row:1/4}}@media(max-width:650px){.task-filters,.fact-list{grid-template-columns:1fr}.task-row{grid-template-columns:1fr;padding:16px}.task-time,.task-row>div:nth-child(3),.task-row>.v-btn{grid-column:auto;grid-row:auto}.task-row>.v-btn{justify-self:start}.identity-line{align-items:flex-start;flex-direction:column}.action-row>.v-btn{flex-grow:1}.full-copy{font-size:18px}}
</style>
