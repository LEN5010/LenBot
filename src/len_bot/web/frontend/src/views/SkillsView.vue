<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtTime } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const rows = ref([]), total = ref(0), loaded = ref(false), loading = ref(false), error = ref(''), message = ref(''), readAt = ref(null)
const scene = ref(route.query.scene || ''), query = ref(route.query.q || '')
const selected = ref(null), detailLoading = ref(false), detailError = ref(''), publishing = ref(false), publishConfirm = ref(false)
const tab = computed(() => route.query.tab === 'candidates' ? 'candidates' : 'saved')
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
let requestId = 0, detailRequest = 0
async function load() {
  const request = ++requestId
  loading.value = true; error.value = ''
  const params = new URLSearchParams({ page: String(page.value), page_size: '30' })
  if (route.query.scene) params.set('scene_id', route.query.scene)
  if (tab.value === 'saved' && route.query.q) params.set('query', route.query.q)
  try {
    const result = await api(`/api/cockpit/${tab.value === 'saved' ? 'skills' : 'skill-candidates'}?${params}`)
    if (request !== requestId) return
    rows.value = result.items; total.value = result.total; loaded.value = true; readAt.value = Date.now()/1000
  } catch (e) { if (request === requestId) error.value = e.message }
  finally { if (request === requestId) loading.value = false }
}
async function loadDetail() {
  const request = ++detailRequest
  selected.value = null; detailError.value = ''; publishConfirm.value = false
  if (!route.query.id) return
  detailLoading.value = true
  const params = new URLSearchParams({ scene_id: route.query.scene || '' })
  if (route.query.version) params.set('version', route.query.version)
  try {
    const result = await api(`/api/cockpit/skills/${encodeURIComponent(route.query.id)}?${params}`)
    if (request === detailRequest) selected.value = result
  } catch (e) { if (request === detailRequest) detailError.value = e.message }
  finally { if (request === detailRequest) detailLoading.value = false }
}
function open(skill) { router.push({ name: 'skills', query: { ...route.query, id: skill.id, scene: skill.scene_id, version: skill.version } }) }
function close() { const query = { ...route.query }; delete query.id; delete query.version; router.push({ name: 'skills', query }) }
function filter() { router.push({ name: 'skills', query: { tab: tab.value, scene: scene.value || undefined, q: query.value || undefined, page: 1 } }) }
async function publish() {
  if (publishing.value || !selected.value) return
  publishing.value = true; detailError.value = ''
  const { id, scene_id, version } = selected.value
  try {
    await api(`/api/cockpit/skills/${encodeURIComponent(id)}/publish`, { method: 'POST', body: JSON.stringify({ scene_id, expected_version: version }) })
    message.value = `已公开技能 v${version}，其他版本的范围保持原记录`; publishConfirm.value = false
    await Promise.all([load(), loadDetail()])
  } catch (e) { detailError.value = e.message }
  finally { publishing.value = false }
}
watch(() => [route.query.scene, route.query.q, route.query.page, route.query.tab], () => {
  rows.value = []; total.value = 0; loaded.value = false; readAt.value = null
  scene.value = route.query.scene || ''; query.value = route.query.q || ''; load()
}, { immediate: true })
watch(() => [route.query.id, route.query.version, route.query.scene], loadDetail, { immediate: true })
</script>
<template>
  <div class="page-stack">
    <PageHeader title="方法技能" description="有来源、有版本的工作方法。读取页面不会生成或执行技能。"><v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn></PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert><v-alert v-if="message" type="success" variant="tonal" closable @click:close="message=''">{{ message }}</v-alert>
    <v-tabs :model-value="tab" color="primary" @update:model-value="value => router.push({name:'skills',query:{scene:route.query.scene,tab:value,page:1}})"><v-tab value="saved">已保存技能</v-tab><v-tab value="candidates">经验候选</v-tab></v-tabs>
    <v-card class="pa-4"><v-form class="filters" @submit.prevent="filter"><ScopeSelect v-model="scene" :include-global="true" clearable /><v-text-field v-if="tab==='saved'" v-model="query" label="名称或适用条件" hide-details clearable /><v-btn type="submit" color="primary">查询</v-btn></v-form></v-card>
    <p class="muted">{{ tab === 'saved' ? '自动技能限来源场景使用，只有运营明确公开的版本可跨场景读取。人工内容不会被自动整理覆盖。' : '重复、没有新增方法价值或来源不足的候选可以正常跳过，并保留原因；失败、中断和过期分别记录。' }}</p>
    <v-progress-linear v-if="loading" indeterminate />
    <p v-if="loaded" class="muted">共 {{ total }} {{ tab === 'saved' ? '项技能' : '条候选' }}</p>
    <div v-if="tab==='saved'" class="skill-list">
      <v-card v-for="skill in rows" :key="skill.id" class="pa-5 skill-row"><div class="skill-main"><h2>{{ skill.name }}</h2><p class="clamp-2">{{ skill.applicability }}</p><div class="meta"><span>{{ skill.author==='human' ? '人工维护' : '工作经验' }}</span><span>{{ skill.scope==='global-safe' ? '此版本已公开' : '仅来源场景' }}</span><EntityLink type="scene" :id="skill.scene_id" /><span>v{{ skill.version }}</span></div></div><v-btn variant="tonal" color="primary" @click="open(skill)">查看正文</v-btn></v-card>
    </div>
    <div v-else class="skill-list"><v-card v-for="candidate in rows" :key="candidate.id" class="pa-5"><div class="candidate-heading"><h2>{{ candidate.candidate.name }}</h2><StatusBadge domain="skill_candidate" :status="candidate.status" /></div><p class="clamp-2 my-3">{{ candidate.candidate.lesson }}</p><div class="meta"><EntityLink type="job" :id="candidate.job_id" :scene-id="candidate.scene_id" /><span>来源工作版本 {{ candidate.job_revision }}</span><EntityLink type="scene" :id="candidate.scene_id" /></div><p v-if="candidate.skip_reason" class="skip-reason">跳过原因：{{ candidate.skip_reason }}</p><v-alert v-if="candidate.error" :type="candidate.status==='obsolete'||candidate.status==='interrupted'?'warning':'error'" variant="tonal" class="mt-3">{{ candidate.error }}</v-alert><v-expansion-panels class="mt-3"><v-expansion-panel title="候选全文与来源"><v-expansion-panel-text><ResourceViewer title="经验候选" :content="candidate.candidate" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></v-card></div>
    <v-card v-if="loaded && !loading && !error && !rows.length" class="pa-8 text-center muted">当前范围没有{{ tab==='saved' ? '已保存技能' : '经验候选' }}</v-card>
    <v-pagination v-if="total>30" :model-value="page" :length="Math.ceil(total/30)" :total-visible="5" @update:model-value="value=>router.push({name:'skills',query:{...route.query,page:value}})" />
    <v-dialog :model-value="!!route.query.id" max-width="900" scrollable :persistent="publishing" @update:model-value="value=>!value&&close()"><v-card><v-card-title class="dialog-title">技能详情<v-btn variant="text" :disabled="publishing" @click="close">关闭</v-btn></v-card-title><v-card-text><v-progress-linear v-if="detailLoading" indeterminate /><v-alert v-if="detailError" type="error" variant="tonal">{{ detailError }}</v-alert><template v-if="selected"><h2>{{ selected.name }}</h2><p class="entity-id my-2">{{ selected.id }}</p><div class="meta mb-5"><span>{{ selected.scene_id }}</span><span>{{ selected.scope==='global-safe' ? '此版本已公开' : '仅来源场景' }}</span><span>{{ selected.author==='human' ? '人工维护' : '工作经验' }}</span><span>{{ fmtTime(selected.updated_at) }}</span></div><v-select :model-value="selected.version" :items="selected.versions.map(item=>({title:`v${item.version} · ${item.scope==='global-safe'?'已公开':'来源场景'} · ${fmtTime(item.created_at)}`,value:item.version}))" label="读取指定版本" @update:model-value="value=>router.push({name:'skills',query:{...route.query,version:value}})" /><section class="skill-body"><h3>适用条件</h3><p>{{ selected.applicability }}</p><h3>步骤</h3><ol><li v-for="(step,i) in selected.steps" :key="i">{{ step }}</li></ol><h3>验证要求</h3><ul><li v-for="(item,i) in selected.verification" :key="i">{{ item }}</li></ul><h3>不适用条件</h3><ul><li v-for="(item,i) in selected.exclusions" :key="i">{{ item }}</li></ul></section><v-divider class="my-5" /><h3 class="mb-3">来源工作与纠正</h3><EntityLink v-if="selected.source.job_id" type="job" :id="selected.source.job_id" :scene-id="selected.scene_id" /><div class="meta my-3"><EntityLink v-for="id in selected.source.correction_event_ids || []" :key="id" type="event" :id="id" :scene-id="selected.scene_id" label="查看纠正原话" /><EntityLink v-for="id in selected.source.result_ids || []" :key="id" type="result" :id="id" :scene-id="selected.scene_id" /></div><ResourceViewer title="完整来源" :content="selected.source" /><v-btn v-if="selected.scope!=='global-safe'" class="mt-5" color="primary" variant="tonal" :disabled="selected.version !== selected.versions[0].version" @click="publishConfirm=true">公开 v{{ selected.version }}</v-btn><p v-if="selected.scope!=='global-safe' && selected.version !== selected.versions[0].version" class="muted mt-3">当前为历史版本。公开接口只接受最新版本，选择最新版本后可提交。</p><v-alert v-if="selected.scope==='global-safe'" class="mt-5" type="info" variant="tonal">v{{ selected.version }} 已明确公开。其他版本的可见范围分别记录。</v-alert></template></v-card-text></v-card></v-dialog>
    <v-dialog v-model="publishConfirm" max-width="560" :persistent="publishing"><v-card v-if="selected" title="公开指定技能版本"><v-card-text><p>确认公开「{{ selected.name }}」v{{ selected.version }}？所有场景的工作都可以读取这一版本的正文与来源信息。</p><p class="muted mt-3">来源范围：{{ selected.scene_id }}。请核对上方完整正文和来源；此操作只公开当前选中的版本。</p></v-card-text><v-card-actions><v-spacer /><v-btn :disabled="publishing" @click="publishConfirm=false">取消</v-btn><v-btn color="primary" :loading="publishing" @click="publish">确认公开 v{{ selected.version }}</v-btn></v-card-actions></v-card></v-dialog>
  </div>
</template>
<style scoped>
.skip-reason{margin:14px 0 0;padding:12px;background:rgb(var(--v-theme-surface-variant));border-radius:8px;font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}
.filters{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.5fr) auto;align-items:center;gap:12px}.skill-list{display:grid;gap:12px}.skill-row,.candidate-heading,.dialog-title{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.skill-main{min-width:0;flex:1}.skill-main h2,.candidate-heading h2{font-size:17px;line-height:1.5;overflow-wrap:anywhere}.skill-main>p{margin:10px 0;line-height:1.65}.meta{display:flex;flex-wrap:wrap;gap:8px 16px;color:#64748b;font-size:13px;align-items:center}.skill-body{line-height:1.8;overflow-wrap:anywhere}.skill-body h3{margin:22px 0 8px}.skill-body p{white-space:pre-wrap}.skill-body ol,.skill-body ul{padding-left:24px}.skill-body li{margin:6px 0;white-space:pre-wrap}.dialog-title{align-items:center}@media(max-width:650px){.filters{grid-template-columns:minmax(0,1fr)}.skill-row,.candidate-heading{flex-direction:column}.skill-row>.v-btn{align-self:flex-start}}
</style>
