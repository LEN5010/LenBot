<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { mdiArrowLeft, mdiRefresh, mdiTextBoxRemoveOutline } from '@mdi/js'
import { api, fmtTime } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EntityLink from '../components/EntityLink.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const id = computed(() => scalar(route.query.id))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const filters = ref({ scene: '', subject: '', status: 'active', kind: '', query: '' })
const rows = ref([]), total = ref(0), pageSize = ref(30), loading = ref(false), listError = ref(''), listLoaded = ref(false), readAt = ref(null)
const memory = ref(null), chain = ref([]), detailLoading = ref(false), detailError = ref(''), detailMissing = ref(false), detailReadAt = ref(null)
const refuteOpen = ref(false), reason = ref(''), saving = ref(false), actionError = ref(''), feedback = ref('')
let listRequest = 0, detailRequest = 0
const dirty = computed(() => refuteOpen.value && Boolean(reason.value.trim()))
const { confirmLeave } = useUnsavedChanges(dirty)
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const kindLabels = { address: '称呼', preference: '偏好', relationship: '关系', fact: '事实', group_norm: '群体规范' }
const kinds = [{ title: '全部类型', value: '' }, ...Object.entries(kindLabels).map(([value, title]) => ({ title, value }))]
const statuses = [{ title: '全部状态', value: '' }, { title: '有效', value: 'active' }, { title: '已替代', value: 'superseded' }, { title: '已撤销', value: 'refuted' }]
function kindName(value) { return kindLabels[value] || '未识别：' + value }
function expired(item) { return item.expires_at !== null && item.expires_at <= Date.now() / 1000 }
function clean(values) { return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== '')) }
function listQuery() { const { id: ignoredId, list_scene: originalScene, ...query } = route.query; if (originalScene !== undefined) query.scene = scalar(originalScene) || undefined; return query }
function detailRoute(item) { return { name: 'memories', query: { ...listQuery(), list_scene: scalar(route.query.scene), id: item.id, scene: item.scope } } }
function open(item) { router.push(detailRoute(item)) }
function close() { router.push({ name: 'memories', query: listQuery() }) }
function applyFilters() { router.push({ name: 'memories', query: { ...clean({ scene: filters.value.scene, subject: (filters.value.subject || '').trim(), kind: filters.value.kind, query: (filters.value.query || '').trim() }), status: filters.value.status, page: 1 } }) }
function changePage(value) { router.push({ name: 'memories', query: { ...route.query, page: value } }) }
async function loadList() {
  const request = ++listRequest
  loading.value = true; listError.value = ''
  const params = new URLSearchParams(clean({ scope: scalar(route.query.scene), subject: scalar(route.query.subject), status: route.query.status === undefined ? 'active' : scalar(route.query.status), kind: scalar(route.query.kind), query: scalar(route.query.query), page: page.value, page_size: 30 }))
  try {
    const result = await api('/api/cockpit/memories?' + params)
    if (request !== listRequest || id.value) return
    rows.value = result.items; total.value = result.total; pageSize.value = result.page_size; listLoaded.value = true; readAt.value = Date.now() / 1000
  } catch (error) { if (request === listRequest) listError.value = error.message }
  finally { if (request === listRequest) loading.value = false }
}
async function loadDetail({ reset = false } = {}) {
  if (!id.value) return
  const current = id.value, request = ++detailRequest, scope = scalar(route.query.scene)
  detailLoading.value = true; detailError.value = ''; detailMissing.value = false
  if (reset) { memory.value = null; chain.value = []; detailReadAt.value = null }
  try {
    const [item, revisions] = await Promise.all([
      api(`/api/cockpit/memories/${encodeURIComponent(current)}` + (scope ? '?scope=' + encodeURIComponent(scope) : '')),
      api(`/api/cockpit/memories/${encodeURIComponent(current)}/chain` + (scope ? '?scope=' + encodeURIComponent(scope) : '')),
    ])
    if (request !== detailRequest || current !== id.value) return
    memory.value = item; chain.value = revisions.chain; detailReadAt.value = Date.now() / 1000
  } catch (error) { if (request === detailRequest) { detailError.value = error.message; detailMissing.value = error.status === 404; if (detailMissing.value) { memory.value = null; chain.value = [] } } }
  finally { if (request === detailRequest) detailLoading.value = false }
}
function startRefute() { if (memory.value.status === 'active' && !saving.value) { refuteOpen.value = true; reason.value = ''; actionError.value = ''; feedback.value = '' } }
function cancelRefute() { if (confirmLeave()) { refuteOpen.value = false; reason.value = '' } }
async function refute() {
  if (saving.value || !reason.value.trim() || !memory.value || memory.value.status !== 'active') return
  const current = memory.value.id, scope = scalar(route.query.scene)
  saving.value = true; ++detailRequest; detailLoading.value = false; actionError.value = ''; feedback.value = ''
  try {
    const result = await api(`/api/cockpit/memories/${encodeURIComponent(current)}/refute`, { method: 'POST', body: JSON.stringify({ reason: reason.value.trim() }) })
    if (current !== id.value || scalar(route.query.scene) !== scope) return
    memory.value = { ...memory.value, status: result.status }
    refuteOpen.value = false; reason.value = ''; feedback.value = '认识已撤销；原记录、原始证据和撤销依据均保留。'
    await loadDetail()
  } catch (error) { if (current === id.value && scalar(route.query.scene) === scope) { actionError.value = error.message; if (error.status === 409) await loadDetail() } }
  finally { saving.value = false }
}
function refresh() { if (saving.value) return; return id.value ? loadDetail() : loadList() }
function onVisible() { if (document.visibilityState === 'visible') refresh() }
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => { ++listRequest; ++detailRequest; document.removeEventListener('visibilitychange', onVisible) })
onBeforeRouteUpdate((to, from) => to.query.id !== from.query.id || to.query.scene !== from.query.scene ? confirmLeave() : true)
watch(() => [route.query.id, route.query.scene], () => {
  ++listRequest; ++detailRequest; refuteOpen.value = false; reason.value = ''; actionError.value = ''; feedback.value = ''
  if (id.value) loadDetail({ reset: true })
}, { immediate: true })
watch(() => [route.query.id, route.query.scene, route.query.subject, route.query.status, route.query.kind, route.query.query, route.query.page], () => {
  filters.value = { scene: scalar(route.query.scene), subject: scalar(route.query.subject), status: route.query.status === undefined ? 'active' : scalar(route.query.status), kind: scalar(route.query.kind), query: scalar(route.query.query) }
  if (!id.value) { rows.value = []; total.value = 0; listLoaded.value = false; readAt.value = null; loadList() }
}, { immediate: true })
</script>

<template>
  <section class="memories-page">
    <PageHeader :title="id ? '认识详情' : '认识与记忆'" description="查看原话报告、有据推断与修订；认识不是原始事实的替代品。">
      <v-btn v-if="id" variant="text" :prepend-icon="mdiArrowLeft" @click="close">返回认识列表</v-btn>
      <v-btn variant="outlined" :prepend-icon="mdiRefresh" :loading="id ? detailLoading : loading" @click="refresh">刷新</v-btn>
    </PageHeader>
    <template v-if="!id">
      <v-card><v-card-text><v-form class="memory-filters" @submit.prevent="applyFilters"><ScopeSelect v-model="filters.scene" clearable /><v-text-field v-model="filters.subject" label="对象账号或场景 ID" hide-details clearable /><v-text-field v-model="filters.query" label="查找认识内容" hide-details clearable /><v-select v-model="filters.status" label="原始状态" :items="statuses" hide-details /><v-select v-model="filters.kind" label="认识类型" :items="kinds" hide-details /><v-btn type="submit" color="primary">筛选</v-btn></v-form></v-card-text></v-card>
      <v-alert v-if="listError" type="error" variant="tonal" title="认识列表读取失败" class="section-gap">{{ listError }}<div v-if="readAt">保留上次读取结果：{{ fmtTime(readAt) }}</div></v-alert><v-progress-linear v-if="loading" indeterminate class="section-gap" aria-label="正在读取认识" />
      <div v-if="listLoaded" class="list-meta"><span>共 {{ total }} 条 · 每页 {{ pageSize }} 条</span><span>读取于 {{ fmtTime(readAt) }}</span></div>
      <div class="memory-list"><v-card v-for="item in rows" :key="item.id" tag="article" class="memory-row"><div class="memory-object"><span class="subject-id">{{ item.subject }}</span><span class="auxiliary">{{ kindName(item.kind) }}</span><EntityLink type="scene" :id="item.scope" :scene-id="item.scope" /></div><div class="memory-copy"><RouterLink :to="detailRoute(item)" class="record-title two-lines">{{ item.statement }}</RouterLink><span class="auxiliary">{{ item.evidence.length }} 条原始证据</span></div><div class="memory-state"><StatusBadge domain="basis" :status="item.basis" /><div class="state-line"><span v-if="expired(item)" class="auxiliary">原状态</span><StatusBadge domain="memory" :status="item.status" /></div><v-chip v-if="expired(item)" color="warning" variant="tonal" size="small">现已过期</v-chip></div><v-btn variant="tonal" @click="open(item)">查看详情</v-btn></v-card></div>
      <v-card v-if="listLoaded && !rows.length && !listError" class="empty-state"><v-card-text>没有符合筛选条件的认识。</v-card-text></v-card><v-pagination v-if="pages > 1" :model-value="page" :length="pages" :total-visible="5" class="section-gap" @update:model-value="changePage" />
    </template>
    <template v-else>
      <v-alert v-if="detailError" :type="detailMissing ? 'warning' : 'error'" variant="tonal" :title="detailMissing ? '认识不存在或不属于此场景' : '认识详情读取失败'">{{ detailError }}<div v-if="detailReadAt">上次读取：{{ fmtTime(detailReadAt) }}</div></v-alert><v-skeleton-loader v-if="detailLoading && !memory" type="article, list-item-three-line" />
      <template v-if="memory">
        <v-card><v-card-text><div class="detail-heading"><div><h2>{{ kindName(memory.kind) }} · {{ memory.subject }}</h2><div class="identity-line"><EntityLink type="memory" :id="memory.id" :scene-id="memory.scope" /><EntityLink type="scene" :id="memory.scope" :scene-id="memory.scope" /></div></div><v-btn v-if="memory.status === 'active'" variant="outlined" color="error" :prepend-icon="mdiTextBoxRemoveOutline" :disabled="saving || refuteOpen" @click="startRefute">撤销认识</v-btn></div><div class="status-line"><StatusBadge domain="basis" :status="memory.basis" /><StatusBadge domain="memory" :status="memory.status" /><v-chip v-if="expired(memory)" color="warning" variant="tonal">现已过期，保留原状态</v-chip></div><p class="auxiliary">创建于 {{ fmtTime(memory.created_at) }} · {{ memory.expires_at === null ? '未设到期时间' : '到期于 ' + fmtTime(memory.expires_at) }} · 读取于 {{ fmtTime(detailReadAt) }}</p><ResourceViewer title="完整认识" :content="memory.statement" /></v-card-text></v-card>
        <v-alert v-if="feedback" type="success" variant="tonal" class="section-gap" role="status">{{ feedback }}</v-alert><v-alert v-if="actionError" type="error" variant="tonal" class="section-gap" role="alert">{{ actionError }}</v-alert>
        <v-card v-if="refuteOpen" class="section-gap refute-card"><v-card-title>确认撤销这条认识</v-card-title><v-card-text><p class="mb-4">记录具体理由后撤销当前认识。原文、证据和修订链会继续保留。</p><v-form @submit.prevent="refute"><v-textarea v-model="reason" label="撤销依据" placeholder="说明哪里不准确，以及已确认的纠正信息" rows="4" auto-grow maxlength="2000" counter :disabled="saving" /><div class="action-row"><v-btn type="submit" color="error" :loading="saving" :disabled="saving || !reason.trim() || memory.status !== 'active'">记录依据并撤销</v-btn><v-btn variant="text" :disabled="saving" @click="cancelRefute">保留认识</v-btn></div></v-form></v-card-text></v-card>
        <v-card class="section-gap"><v-card-title>原始证据</v-card-title><v-card-text><div class="link-list"><EntityLink v-for="eventId in memory.evidence" :key="eventId" type="event" :id="eventId" :scene-id="memory.scope" /></div><p v-if="!memory.evidence.length" class="auxiliary">此记录没有附原始证据。</p></v-card-text></v-card>
        <v-card class="section-gap"><v-card-title>修订时间线</v-card-title><v-card-text><ol class="revision-list"><li v-for="item in chain" :key="item.id" class="revision-item"><div class="revision-heading"><time>{{ fmtTime(item.created_at) }}</time><StatusBadge domain="memory" :status="item.status" /><StatusBadge domain="basis" :status="item.basis" /><EntityLink type="memory" :id="item.id" :scene-id="item.scope" /></div><p class="full-copy">{{ item.statement }}</p><p v-if="item.revision_reason" class="full-copy">修订依据：{{ item.revision_reason }}</p><v-expansion-panels variant="accordion"><v-expansion-panel title="原始证据与修订关联"><v-expansion-panel-text><h3>原始证据</h3><div class="link-list"><EntityLink v-for="eventId in item.evidence" :key="eventId" type="event" :id="eventId" :scene-id="item.scope" /></div><h3 v-if="item.revision_evidence.length">修订证据</h3><div class="link-list"><EntityLink v-for="eventId in item.revision_evidence" :key="eventId" type="event" :id="eventId" :scene-id="item.scope" /></div><h3 v-if="item.supersedes_ids.length || item.superseded_by">替代关系</h3><div class="link-list"><EntityLink v-for="memoryId in item.supersedes_ids" :key="memoryId" type="memory" :id="memoryId" :scene-id="item.scope" /><EntityLink v-if="item.superseded_by" type="memory" :id="item.superseded_by" :scene-id="item.scope" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></li></ol><p v-if="!chain.length" class="auxiliary">没有找到修订记录。</p></v-card-text></v-card>
      </template>
    </template>
  </section>
</template>

<style scoped>
.memories-page{min-width:0}.memories-page>.page-header{margin-bottom:24px}.memory-filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:center}.memory-filters>.scope-select{width:100%}.section-gap{margin-top:20px}.list-meta{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0;font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.memory-list{display:grid;gap:12px}.memory-row{display:grid;grid-template-columns:minmax(130px,170px) minmax(0,1fr) minmax(112px,150px) auto;gap:20px;align-items:start;padding:18px}.memory-object,.memory-copy,.memory-state{min-width:0;display:grid;gap:8px}.subject-id{font-weight:600;white-space:nowrap;text-overflow:ellipsis;overflow:hidden}.record-title{font-size:15px;font-weight:500;line-height:1.65;color:rgb(var(--v-theme-primary));text-decoration:none}.record-title:hover{text-decoration:underline}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.memory-state{justify-items:start}.state-line,.status-line,.identity-line,.action-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.auxiliary{font-size:13px;color:rgb(var(--v-theme-on-surface-variant));line-height:1.5}.empty-state{text-align:center;padding:20px}.detail-heading{display:flex;justify-content:space-between;gap:20px;align-items:start}.detail-heading>div{min-width:0}.detail-heading h2{font-size:20px;line-height:1.55;overflow-wrap:anywhere}.identity-line,.status-line{margin:12px 0}.full-copy{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.75;margin:14px 0}.link-list{display:grid;gap:10px;min-width:0}.revision-list{list-style:none;margin:0;padding:0}.revision-item{border-left:2px solid var(--line);padding:0 0 24px 20px;margin:0 0 12px}.revision-item:last-child{padding-bottom:0;margin-bottom:0}.revision-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.revision-heading time{font-size:13px;color:rgb(var(--v-theme-on-surface-variant))}.revision-item h3{font-size:15px;margin:16px 0 10px}.refute-card{max-width:900px}@media(max-width:1150px){.memory-row{grid-template-columns:minmax(0,1fr) auto}.memory-object{grid-column:1;grid-row:1}.memory-copy{grid-column:1/-1;grid-row:2}.memory-state{grid-column:1;grid-row:3;display:flex;flex-wrap:wrap;align-items:center}.memory-row>.v-btn{grid-column:2;grid-row:1}.memory-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){.memory-filters{grid-template-columns:1fr}.memory-row{padding:16px;gap:14px}.memory-object{max-width:100%}.memory-row>.v-btn{grid-row:4;grid-column:1;justify-self:start}.memory-copy{grid-column:1}.memory-state{grid-column:1}.detail-heading{flex-direction:column}.identity-line{align-items:flex-start;flex-direction:column}.detail-heading h2{font-size:18px}.revision-item{padding-left:14px}.action-row>.v-btn{flex-grow:1}}
</style>
