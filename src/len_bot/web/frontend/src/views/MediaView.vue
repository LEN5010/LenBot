<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const route = useRoute(), router = useRouter()
const assets = ref([]), total = ref(0), palette = ref([]), loaded = ref(false), loading = ref(false)
const error = ref(''), message = ref(''), readAt = ref(null), broken = ref(new Set())
const query = ref(route.query.q || ''), scope = ref(route.query.scene || 'global-safe')
const kind = ref(route.query.kind || 'all'), enabled = ref(route.query.enabled || 'all')
const selected = ref(null), detailLoading = ref(false), detailError = ref(''), draft = ref(null), original = ref('')
const saving = ref(false), uploadOpen = ref(false), uploading = ref(false)
const uploadForm = ref({ scope: 'global-safe', description: '', tags: '', file: null })
const uploadError = ref('')
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== original.value)
const uploadDirty = computed(() => uploadOpen.value && !!(uploadForm.value.file || uploadForm.value.description || uploadForm.value.tags))
const { confirmLeave } = useUnsavedChanges(computed(() => dirty.value || uploadDirty.value))
onBeforeRouteUpdate((to, from) => (to.query.id !== from.query.id || to.query.scene !== from.query.scene) ? confirmLeave() : true)
let listRequest = 0, detailRequest = 0
const preview = asset => `/api/media/${encodeURIComponent(asset.id)}/file?scene_id=${encodeURIComponent(asset.scope)}`
const toAsset = asset => ({ name: 'media', query: { ...route.query, scene: route.query.scene || 'global-safe', id: asset.id } })
function setDraft(asset) {
  selected.value = asset
  draft.value = asset.curated ? { scope: asset.scope, description: asset.description, tagsText: asset.tags.join(' '), enabled: asset.enabled, palette_order: asset.palette_order } : null
  original.value = JSON.stringify(draft.value)
}
async function load() {
  const request = ++listRequest
  loading.value = true; error.value = ''; broken.value = new Set()
  const params = new URLSearchParams({ scene_id: route.query.scene || 'global-safe', query: route.query.q || '', page: String(page.value), page_size: '48' })
  if (route.query.kind && route.query.kind !== 'all') params.set('curated', String(route.query.kind === 'curated'))
  if (route.query.enabled && route.query.enabled !== 'all') params.set('enabled', String(route.query.enabled === 'enabled'))
  try {
    const [result, catalog] = await Promise.all([api('/api/media?' + params), api('/api/media/palette?' + new URLSearchParams({ scene_id: params.get('scene_id') }))])
    if (request !== listRequest) return
    assets.value = result.items; total.value = result.total; palette.value = catalog.items; loaded.value = true; readAt.value = Date.now() / 1000
  } catch (e) { if (request === listRequest) error.value = e.message }
  finally { if (request === listRequest) loading.value = false }
}
async function loadDetail() {
  const request = ++detailRequest, id = route.query.id
  selected.value = null; draft.value = null; original.value = ''; detailError.value = ''
  if (!id) return
  detailLoading.value = true
  try {
    const asset = await api(`/api/media/${encodeURIComponent(id)}?` + new URLSearchParams({ scene_id: route.query.scene || 'global-safe' }))
    if (request === detailRequest) setDraft(asset)
  } catch (e) { if (request === detailRequest) detailError.value = e.message }
  finally { if (request === detailRequest) detailLoading.value = false }
}
function filter() {
  router.push({ name: 'media', query: { scene: scope.value || 'global-safe', q: query.value || undefined, kind: kind.value === 'all' ? undefined : kind.value, enabled: enabled.value === 'all' ? undefined : enabled.value, page: 1 } })
}
function closeDetail() { const query = { ...route.query }; delete query.id; router.push({ name: 'media', query }) }
function markBroken(id) { broken.value = new Set([...broken.value, id]) }
async function save() {
  if (saving.value || !draft.value) return
  const changedAccess = draft.value.enabled !== selected.value.enabled || draft.value.palette_order !== selected.value.palette_order
  if (changedAccess && !window.confirm('保存后将按新的启用状态和顺序更新模型可用素材与固定目录。确认保存？')) return
  saving.value = true; detailError.value = ''; message.value = ''
  try {
    const { tagsText, ...body } = draft.value
    body.tags = [...new Set(tagsText.split(/\s+/).filter(Boolean))]
    const result = await api(`/api/media/${encodeURIComponent(selected.value.id)}`, { method: 'POST', body: JSON.stringify(body) })
    setDraft(result); message.value = '素材与固定目录已保存'; await load()
  } catch (e) { detailError.value = e.message }
  finally { saving.value = false }
}
function openUpload() {
  uploadForm.value = { scope: scope.value || 'global-safe', description: '', tags: '', file: null }; uploadError.value = ''; uploadOpen.value = true
}
function closeUpload() {
  if (uploading.value) return
  if (uploadDirty.value && !window.confirm('放弃尚未上传的素材表单？')) return
  uploadOpen.value = false
}
async function upload() {
  if (uploading.value || !uploadForm.value.file || !uploadForm.value.scope) return
  uploading.value = true; uploadError.value = ''
  try {
    const form = new FormData()
    form.append('file', uploadForm.value.file); form.append('scope', uploadForm.value.scope)
    form.append('description', uploadForm.value.description); form.append('tags', uploadForm.value.tags)
    const asset = await api('/api/media', { method: 'POST', body: form })
    uploadOpen.value = false; message.value = '素材已上传，可在详情中设置固定目录顺序'
    await router.push({ name: 'media', query: { scene: asset.scope, id: asset.id, page: 1 } }); await load()
  } catch (e) { uploadError.value = e.message }
  finally { uploading.value = false }
}
watch(() => [route.query.scene, route.query.q, route.query.kind, route.query.enabled, route.query.page], () => {
  assets.value = []; palette.value = []; total.value = 0; loaded.value = false; readAt.value = null
  scope.value = route.query.scene || 'global-safe'; query.value = route.query.q || ''; kind.value = route.query.kind || 'all'; enabled.value = route.query.enabled || 'all'; load()
}, { immediate: true })
watch(() => [route.query.id, route.query.scene], loadDetail, { immediate: true })
</script>

<template>
  <div class="page-stack">
    <PageHeader title="图片与表情" description="原图、运营素材与固定目录。每张图片保留自己的来源范围。">
      <v-btn variant="outlined" :loading="loading" @click="load">刷新</v-btn><v-btn color="primary" @click="openUpload">上传素材</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span></v-alert>
    <v-alert v-if="message" type="success" variant="tonal" closable @click:close="message = ''">{{ message }}</v-alert>
    <v-card class="pa-4">
      <v-form class="filters" @submit.prevent="filter">
        <ScopeSelect v-model="scope" :include-global="true" :clearable="false" />
        <v-text-field v-model="query" label="描述或标签" hide-details clearable />
        <v-select v-model="kind" :items="[{title:'全部来源',value:'all'},{title:'运营素材',value:'curated'},{title:'聊天原图',value:'chat'}]" label="来源" hide-details />
        <v-select v-model="enabled" :items="[{title:'全部状态',value:'all'},{title:'已启用',value:'enabled'},{title:'已停用',value:'disabled'}]" label="状态" hide-details />
        <v-btn type="submit" color="primary" :disabled="loading">查询</v-btn>
      </v-form>
    </v-card>
    <v-expansion-panels>
      <v-expansion-panel title="固定表情目录" :text="!palette.length ? '当前范围尚无固定目录素材。' : undefined">
        <v-expansion-panel-text v-if="palette.length">
          <p class="muted mb-3">最多 20 项，按运营顺序提供。这里的顺序独立于下面的网格分页。</p>
          <div class="palette-list"><RouterLink v-for="asset in palette" :key="asset.id" :to="toAsset(asset)" class="palette-item"><img :src="preview(asset)" :alt="asset.description || '固定目录素材'" loading="lazy" /><span class="clamp-2">{{ asset.palette_order }} · {{ asset.description || asset.id }}</span></RouterLink></div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
    <div v-if="loaded" class="list-summary"><span>{{ total }} 张素材</span><span v-if="readAt" class="muted">读取于 {{ fmtTime(readAt) }}</span></div>
    <v-progress-linear v-if="loading" indeterminate aria-label="正在读取素材" />
    <div class="media-grid">
      <v-card v-for="asset in assets" :key="asset.id" tag="article">
        <RouterLink :to="toAsset(asset)" class="asset-link">
          <div class="asset-preview"><img v-if="!broken.has(asset.id)" :src="preview(asset)" :alt="asset.description || '图片预览'" loading="lazy" @error="markBroken(asset.id)" /><span v-else class="muted">图片不可读</span></div>
          <div class="asset-copy"><strong class="clamp-2">{{ asset.description || (asset.curated ? '运营素材' : '聊天原图') }}</strong><p class="muted asset-scope">{{ asset.scope }}</p><div class="chips"><v-chip size="small" :color="asset.enabled ? 'success' : 'default'">{{ asset.enabled ? '已启用' : '已停用' }}</v-chip><v-chip v-if="asset.palette_order != null" size="small" color="primary">目录 {{ asset.palette_order }}</v-chip></div><p class="asset-tags clamp-2">{{ asset.tags.slice(0,3).join(' · ') }}<span v-if="asset.tags.length > 3"> · +{{ asset.tags.length - 3 }}</span></p></div>
        </RouterLink>
      </v-card>
    </div>
    <v-card v-if="loaded && !loading && !error && !assets.length" class="pa-8 text-center muted">当前范围没有匹配素材</v-card>
    <v-pagination v-if="total > 48" :model-value="page" :length="Math.ceil(total/48)" :total-visible="5" @update:model-value="value => router.push({name:'media',query:{...route.query,page:value}})" />

    <v-dialog :model-value="!!route.query.id" max-width="920" scrollable :persistent="saving" @update:model-value="value => !value && closeDetail()">
      <v-card><v-card-title class="dialog-title">素材详情<v-btn variant="text" :disabled="saving" @click="closeDetail">关闭</v-btn></v-card-title><v-card-text>
        <v-progress-linear v-if="detailLoading" indeterminate />
        <v-alert v-if="detailError" type="error" variant="tonal" class="mb-4">{{ detailError }}</v-alert>
        <template v-if="selected">
          <p class="entity-id mb-3">{{ selected.id }}</p><div class="detail-image"><img :src="preview(selected)" :alt="selected.description || '素材原图'" /></div>
          <div class="detail-meta"><v-chip>{{ selected.curated ? '运营素材' : '聊天原图 · 只读' }}</v-chip><span>{{ selected.scope }}</span><span>{{ fmtTime(selected.created_at) }}</span><a :href="preview(selected)" target="_blank" rel="noopener">打开原图</a></div>
          <v-form v-if="draft" :disabled="saving" class="edit-form" @submit.prevent="save"><v-textarea v-model="draft.description" label="完整描述" maxlength="2000" auto-grow rows="3" /><v-text-field v-model="draft.tagsText" label="标签（空格分隔）" /><v-select v-model="draft.palette_order" label="固定目录顺序" :items="[{title:'不在固定目录中展示',value:null},...Array.from({length:20},(_,i)=>({title:String(i+1),value:i+1}))]" /><v-switch v-model="draft.enabled" label="启用素材" color="primary" hide-details /><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty">保存素材与目录</v-btn></v-form>
          <template v-else><p class="full-text">{{ selected.description || '没有描述' }}</p><div class="chips"><v-chip v-for="tag in selected.tags" :key="tag" size="small">{{ tag }}</v-chip></div><p class="muted my-4">聊天原图作为原始资料保留，不能在这里改写或停用。</p></template>
          <v-divider class="my-4" /><h3 class="mb-2">来源记录</h3><EntityLink v-if="selected.source_event_id" type="event" :id="selected.source_event_id" :scene-id="selected.scope" /><p v-else class="muted">未记录来源事件</p>
        </template>
      </v-card-text></v-card>
    </v-dialog>
    <v-dialog :model-value="uploadOpen" max-width="640" :persistent="uploading" @update:model-value="value => !value && closeUpload()"><v-card><v-card-title class="dialog-title">上传运营素材<v-btn variant="text" :disabled="uploading" @click="closeUpload">关闭</v-btn></v-card-title><v-card-text><v-form class="edit-form" :disabled="uploading" @submit.prevent="upload"><v-alert v-if="uploadError" type="error" variant="tonal">{{ uploadError }}</v-alert><p class="muted">支持 PNG、JPEG、WEBP、GIF，最大 10MB。公共素材可在所有场景使用。</p><ScopeSelect v-model="uploadForm.scope" :include-global="true" :clearable="false" /><v-file-input v-model="uploadForm.file" accept="image/png,image/jpeg,image/webp,image/gif" label="图片文件" show-size required /><v-textarea v-model="uploadForm.description" label="描述用途或情绪" maxlength="2000" rows="3" /><v-text-field v-model="uploadForm.tags" label="标签（空格分隔）" /><p>将保存到：<strong>{{ uploadForm.scope || '请选择范围' }}</strong></p><v-btn type="submit" color="primary" :loading="uploading" :disabled="!uploadForm.file || !uploadForm.scope">上传到所选范围</v-btn></v-form></v-card-text></v-card></v-dialog>
  </div>
</template>
<style scoped>
.filters{display:grid;grid-template-columns:minmax(170px,1.2fr) minmax(160px,1.5fr) minmax(120px,.7fr) minmax(120px,.7fr) auto;gap:12px;align-items:center}.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px}.asset-link{display:block;color:inherit;text-decoration:none;height:100%}.asset-preview{aspect-ratio:1;background:#f4f6f9;display:flex;align-items:center;justify-content:center;padding:12px}.asset-preview img{width:100%;height:100%;object-fit:contain}.asset-copy{padding:14px;min-width:0;display:grid;gap:8px}.asset-copy strong{line-height:1.5;min-height:3em;overflow-wrap:anywhere}.asset-scope{font-size:12px;overflow-wrap:anywhere}.asset-tags{font-size:12px;color:#64748b;min-height:1.5em}.chips,.detail-meta{display:flex;gap:8px;flex-wrap:wrap}.list-summary,.dialog-title{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}.palette-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}.palette-item{display:flex;gap:10px;align-items:center;min-width:0;text-decoration:none;color:inherit}.palette-item img{width:48px;height:48px;object-fit:contain;background:#f4f6f9}.detail-image{height:clamp(220px,42vh,450px);background:#f4f6f9;display:grid;place-items:center;margin-bottom:16px}.detail-image img{width:100%;height:100%;object-fit:contain;min-height:0}.detail-meta{font-size:13px;align-items:center;margin-bottom:20px}.edit-form{display:grid;gap:12px}.edit-form>.v-btn{justify-self:start}.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}.asset-link:focus-visible{outline:3px solid #2563eb;outline-offset:-3px}@media(max-width:1000px){.filters{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:500px){.filters{grid-template-columns:minmax(0,1fr)}.media-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.asset-copy{padding:10px}.chips{gap:4px}.detail-image{height:260px}}
</style>
