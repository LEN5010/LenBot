<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteUpdate } from 'vue-router'
import { api, fmtTime } from '../api.js'
import PageHeader from '../components/PageHeader.vue'
import ScopeSelect from '../components/ScopeSelect.vue'
import EntityLink from '../components/EntityLink.vue'
import MediaPreview from '../components/MediaPreview.vue'
import CharacterReferencesPanel from '../components/CharacterReferencesPanel.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { hasConfigDraftChanges, rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from '../components/ConfigConflictBanner.vue'
import ResourceViewer from '../components/ResourceViewer.vue'

const route = useRoute(), router = useRouter()
const scalar = value => typeof value === 'string' ? value : ''
const listScope = computed(() => scalar(route.query.list_scene ?? route.query.scene) || 'global-safe')
const detailKey = computed(() => JSON.stringify([route.query.id, route.query.scene]))
const assets = ref([]), total = ref(0), palette = ref([]), loaded = ref(false), loading = ref(false)
const error = ref(''), readAt = ref(null), uploadMaxBytes = ref(null)
const paletteLoading = ref(false),
  paletteLoaded = ref(false),
  paletteError = ref(''),
  paletteReadAt = ref(null),
  paletteLimit = ref(null)
const query = ref(route.query.q || ''), scope = ref(listScope.value)
const kind = ref(route.query.kind || 'all'), enabled = ref(route.query.enabled || 'all')
const purpose = ref(route.query.purpose || 'all'), referenceEditor = ref(null)
const purposeLabels = {character_reference:'人物参考',sticker:'反应表情',media:'一般媒体'}
const selected = ref(null),
  detailLoading = ref(false),
  detailError = ref(''),
  draft = ref(null),
  baseline = ref(null)
const saveError = ref(''),
  detailMessage = ref(''),
  readbackPending = ref(false),
  savedNotice = ref(null)
const saveReview = ref(null)
const conflicts = useConfigConflicts(), currentConflict = computed(()=>conflicts.entries.media)
const detailReadAt = ref(null), selectedKey = ref(null)
const saving = ref(false), uploadOpen = ref(false), uploading = ref(false)
const uploadForm = ref({ scope: 'global-safe', description: '', tags: '', file: null })
const uploadError = ref('')
const uploadReceipt = ref(null), uploadReview = ref(null)
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const dirty = computed(() => !!draft.value && !!baseline.value && hasConfigDraftChanges(editableAsset(baseline.value), draftValues()))
const uploadDirty = computed(() => uploadOpen.value && !uploadReceipt.value && !!(uploadForm.value.file || uploadForm.value.description || uploadForm.value.tags))
const paletteValid = computed(()=>!draft.value || draft.value.palette_order===null || Number.isSafeInteger(draft.value.palette_order)&&draft.value.palette_order>=0)
const purposeValid = computed(()=>!draft.value || !draftValues().tags.includes('人物参考') || !draftValues().tags.includes('表情包')&&draft.value.palette_order===null)
const changedSinceEdit = computed(()=>!!baseline.value&&!!selected.value&&hasConfigDraftChanges(baseline.value,assetBaseline(selected.value)))
const { confirmLeave } = useUnsavedChanges(computed(() => dirty.value || uploadDirty.value || !!saveReview.value))
onBeforeRouteUpdate((to, from) => (to.query.id !== from.query.id || to.query.scene !== from.query.scene || (uploadOpen.value && to.fullPath !== from.fullPath)) ? confirmLeave() : true)
const detailSelection = () => JSON.stringify([route.name,detailKey.value])
const listGuard = useRequestGuard(()=>JSON.stringify([
  route.name,
  listScope.value,
  route.query.q,
  route.query.kind,
  route.query.enabled,
  route.query.purpose,
  page.value
]))
const paletteGuard = useRequestGuard(()=>JSON.stringify([route.name,listScope.value]))
const detailGuard = useRequestGuard(detailSelection), saveGuard = useRequestGuard(detailSelection)
const uploadGuard = useRequestGuard(()=>JSON.stringify([route.name,route.fullPath,uploadOpen.value]))
const clone = value=>JSON.parse(JSON.stringify(value))
const baselineFields = [
  'id',
  'scope',
  'source_event_id',
  'created_at',
  'curated',
  'description',
  'tags',
  'enabled',
  'palette_order'
]
const assetBaseline = asset=>clone(Object.fromEntries(baselineFields.map(key=>[key,asset[key]])))
const editableAsset = asset=>({
  description:asset.description,
  tags:[...asset.tags],
  enabled:asset.enabled,
  palette_order:asset.palette_order
})
const draftValues = () => ({
  description:draft.value.description,
  tags:[...new Set(draft.value.tagsText.split(/\s+/).filter(Boolean))],
  enabled:draft.value.enabled,
  palette_order:draft.value.palette_order
})
const sameAssetIdentity = (asset, original) => ['id','scope','source_event_id','created_at','curated'].every(key=>asset[key]===original[key])
function savedReceipt(details, scope, id=null) {
  if (details?.asset_saved!==true || typeof details.asset_id!=='string' || !details.asset_id || (id!==null && details.asset_id!==id)
    || details.scope!==scope || typeof details.event_id!=='string' || !details.event_id || !['event','readback'].includes(details.phase)) return null
  return {
    asset_id:details.asset_id,
    scope:details.scope,
    event_id:details.event_id,
    phase:details.phase
  }
}
function requestRefused(error) {
  return error.details!=null && error.details?.asset_saved!==true && [400,401,403,422].includes(error.status)
}
const preview = asset => `/api/media/${encodeURIComponent(asset.id)}/file?scene_id=${encodeURIComponent(asset.scope)}`
const toAsset = asset => ({
  name: 'media',
  query: {
    ...route.query,
    list_scene: listScope.value,
    scene: listScope.value,
    id: asset.id
  }
})
function setDraft(asset) {
  const original = asset.curated ? assetBaseline(asset) : null
  const values = asset.curated ? {
    description: asset.description,
    tagsText: asset.tags.join(' '),
    enabled: asset.enabled,
    palette_order: asset.palette_order
  } : null
  selected.value = asset
  baseline.value = original
  draft.value = values
}
async function load() {
  const fresh = listGuard()
  loading.value = true;
  error.value = ''
  const params = new URLSearchParams({
    scene_id: listScope.value,
    query: route.query.q || '',
    page: String(page.value),
    page_size: '48'
  })
  if (route.query.kind && route.query.kind !== 'all') params.set('curated', String(route.query.kind === 'curated'))
  if (route.query.enabled && route.query.enabled !== 'all') params.set('enabled', String(route.query.enabled === 'enabled'))
  if (route.query.purpose && route.query.purpose !== 'all') params.set('purpose', route.query.purpose)
  try {
    const result = await api('/api/media?' + params)
    if (!fresh()) return
    assets.value = result.items;
    total.value = result.total;
    uploadMaxBytes.value = result.upload_max_bytes;
    loaded.value = true;
    readAt.value = Date.now() / 1000
  } catch (e) {
    if (fresh()) error.value = e.message
  }
  finally {
    if (fresh()) loading.value = false
  }
}
async function loadPalette() {
  const fresh = paletteGuard()
  paletteLoading.value = true;
  paletteError.value = ''
  try {
    const value = await api('/api/media/palette?' + new URLSearchParams({ scene_id: listScope.value }))
    if (!fresh()) return
    palette.value = value.items;
    paletteLimit.value = value.limit;
    paletteLoaded.value = true;
    paletteReadAt.value = Date.now()/1000
  } catch (e) {
    if (fresh()) paletteError.value = e.message
  }
  finally {
    if (fresh()) paletteLoading.value = false
  }
}
async function loadDetail({accept=()=>true}={}) {
  const own=detailGuard(),
    fresh=()=>own()&&accept(),
    id=scalar(route.query.id),
    key=detailKey.value,
    scene=scalar(route.query.scene)||'global-safe'
  if (selectedKey.value !== key) {
    selected.value = null;
    draft.value = null;
    baseline.value = null;
    detailReadAt.value = null;
    selectedKey.value = null
  }
  detailError.value = ''
  conflicts.beginRead('media')
  if(saveReview.value){
    saveReview.value.current=null;
    saveReview.value.readAt=null
  }
  if (!id) {
    detailLoading.value = false;
    return
  }
  detailLoading.value = true
  try {
    const asset = await api(`/api/media/${encodeURIComponent(id)}?` + new URLSearchParams({ scene_id: scene }))
    if (!fresh()) return
    if (!asset || asset.id !== id || ![scene,'global-safe'].includes(asset.scope)) throw new Error('读取结果不属于当前素材与查看范围，未采用。')
    if (saveReview.value) {
      if(asset.scope!==saveReview.value.scope)throw new Error('读取结果的保存范围与原请求不同，原请求结果仍未确认。')
      saveReview.value.current=asset;
      saveReview.value.readAt=Date.now()/1000
    }
    else if (readbackPending.value) {
      if(!sameAssetIdentity(asset,baseline.value))throw new Error('读回的素材身份或原来源已改变，不能用它确认原保存请求。')
      setDraft(asset);
      readbackPending.value=false
    }
    else if (!conflicts.capture('media',asset) && !dirty.value) setDraft(asset)
    selected.value=asset;
    selectedKey.value=key;
    detailReadAt.value=Date.now()/1000
  } catch (e) {
    if (fresh()) {
      detailError.value=e.message;
      conflicts.readFailed('media',e)
    }
  }
  finally {
    if (fresh()) detailLoading.value = false
  }
}
function filter() {
  router.push({
    name: 'media',
    query: {
      return_to: route.query.return_to,
      scene: scope.value || 'global-safe',
      q: query.value || undefined,
      kind: kind.value === 'all' ? undefined : kind.value,
      enabled: enabled.value === 'all' ? undefined : enabled.value,
      purpose: purpose.value === 'all' ? undefined : purpose.value,
      page: 1
    }
  })
}
async function addReference() {
  if (dirty.value || saving.value || detailLoading.value || readbackPending.value || saveReview.value || currentConflict.value
    || !selected.value?.curated || selected.value.purpose!=='character_reference' || !selected.value.enabled || selected.value.palette_order!==null) return
  if (referenceEditor.value?.addAsset(selected.value)) {
    await closeDetail()
    await nextTick()
    document.getElementById('character-references')?.scrollIntoView({block:'start'})
  }
}
function closeDetail() {
  const query = { ...route.query, scene: listScope.value };
  delete query.id;
  delete query.list_scene;
  return router.push({ name: 'media', query })
}
function refresh() {
  load();
  loadPalette()
}
function refreshDetail() {
  if (!saving.value) loadDetail()
}
function setPaletteOrder(value) {
  if(draft.value&&!saving.value&&!detailLoading.value&&!readbackPending.value&&!saveReview.value)draft.value.palette_order = value === '' || value === null ? null : Number(value)
}
function adoptReviewedValues() {
  const current=saveReview.value?.current
  if(saving.value||detailLoading.value||!current)return
  if(!window.confirm('放弃这次未确认请求的旧草稿，采用本次读取的当前值重新编辑？这不确认旧请求的结果，也不会重新发送旧请求。'))return
  setDraft(current);
  saveReview.value=null;
  saveError.value=''
  detailMessage.value='已采用本次读取的当前值重新编辑；旧请求的结果仍未确认，没有重发旧草稿。'
}
function resolveConflict(keep) {
  const asset=currentConflict.value?.snapshot
  if(saving.value||detailLoading.value||readbackPending.value||!asset)return
  if(!keep&&!window.confirm('放弃当前素材的未保存修改，采用本次读取的现值？'))return
  if(keep&&!paletteValid.value){
    saveError.value='请先将目录顺序修正为非负整数或留空，再保留修改。';
    return
  }
  if(keep&&(!asset.curated||['id','scope','source_event_id','created_at'].some(key=>asset[key]!==baseline.value[key]))) {
    saveError.value='原素材身份或来源已改变，须采用现值重新编辑，不能把旧草稿接到新素材。';
    return
  }
  const next=keep?rebaseConfigDraft(editableAsset(baseline.value),draftValues(),editableAsset(asset)):null
  setDraft(asset)
  if(keep)draft.value={
    description:next.description,
    tagsText:next.tags.join(' '),
    enabled:next.enabled,
    palette_order:next.palette_order
  }
  conflicts.clear('media');
  saveError.value='';
  detailMessage.value=keep?'已保留实际修改，其余采用现值；请核对后另行保存。':'已采用本次读取的保存值，没有提交修改。'
}
async function save() {
  if (saving.value || detailLoading.value || detailError.value || readbackPending.value || saveReview.value || currentConflict.value || !dirty.value || !draft.value || !baseline.value || !selected.value?.curated) return
  if (!paletteValid.value) {
    saveError.value = '固定目录顺序须为非负整数，或留空不列入目录。';
    return
  }
  if (!purposeValid.value) {
    saveError.value = '人物参考请移除表情包标签，并将固定目录顺序留空。';
    return
  }
  const changedAccess = draft.value.enabled !== baseline.value.enabled || draft.value.palette_order !== baseline.value.palette_order
  if (changedAccess && !window.confirm(`保存到「${baseline.value.scope}」，更新素材启用状态与固定目录。${baseline.value.scope==='global-safe'?'公共素材会影响所有场景的可用目录。':''}确认保存？`)) return
  const fresh=saveGuard(),key=detailKey.value,id=baseline.value.id,scope=baseline.value.scope
  detailGuard();
  detailLoading.value=false
  saving.value=true;
  saveError.value='';
  detailMessage.value='';
  savedNotice.value=null
  const body = {scope,baseline:clone(baseline.value),...draftValues()}
  try {
    const result = await api(`/api/media/${encodeURIComponent(id)}`, { method: 'POST', body: JSON.stringify(body) })
    if (!fresh()) return
    if (!result||!sameAssetIdentity(result,body.baseline)) throw new Error('保存响应没有返回同一运营素材及原来源；请重读当前素材核对，不直接重复保存。')
    setDraft(result);
    selectedKey.value = key;
    detailReadAt.value = Date.now()/1000
    detailMessage.value='素材与固定目录已保存；目录是否进入本次限额请看当前目录读取。';
    await Promise.all([load(),loadPalette()])
  } catch (e) {
    if(!fresh())return
    const receipt=savedReceipt(e.details,scope,id)
    if(receipt) {
      readbackPending.value=true;
      savedNotice.value=receipt;
      saveError.value=e.message
    }
    else if(e.details?.asset_saved===false&&conflicts.mark('media',e))await loadDetail({accept:fresh})
    else if(requestRefused(e))saveError.value=e.message
    else {
      saveReview.value={...body,current:null,readAt:null}
      saveError.value='未取得属于本次素材保存的有效确认，结果未知。接口信息：'+e.message
    }
  }
  finally {
    if (fresh()) saving.value = false
  }
}
function openUpload() {
  if(saving.value||uploading.value||uploadOpen.value&&!uploadReceipt.value)return
  const nextScope=uploadReceipt.value?.scope||scope.value||'global-safe'
  uploadGuard();
  uploadForm.value={scope:nextScope,description:'',tags:'',file:null}
  uploadError.value='';
  uploadReceipt.value=null;
  uploadReview.value=null;
  uploadOpen.value=true
}
function closeUpload() {
  if (uploading.value) return
  if (uploadDirty.value && !window.confirm(uploadReview.value?'关闭表单不会撤销已发出的请求；请先核对原范围记录，不要直接重复上传。确认关闭？':'放弃尚未上传的素材表单？')) return
  const savedHere=uploadReceipt.value?.scope===listScope.value
  uploadOpen.value=false;
  uploadGuard();
  uploadForm.value={scope:listScope.value,description:'',tags:'',file:null}
  if(savedHere)refresh()
}
async function upload() {
  if (uploading.value || uploadReceipt.value || uploadReview.value || !uploadForm.value.file || !uploadForm.value.scope) return
  const fresh=uploadGuard(),
    attempt={
      scope:uploadForm.value.scope,
      name:uploadForm.value.file.name,
      size:uploadForm.value.file.size
    }
  uploading.value = true;
  uploadError.value = ''
  try {
    const form = new FormData()
    form.append('file', uploadForm.value.file);
    form.append('scope', uploadForm.value.scope)
    form.append('description', uploadForm.value.description);
    form.append('tags', uploadForm.value.tags)
    const asset = await api('/api/media', { method: 'POST', body: form })
    if (!fresh()) return
    if(!asset||typeof asset.id!=='string'||!asset.id||asset.scope!==attempt.scope||asset.curated!==true)throw new Error('上传响应没有确认所选范围的运营素材；请核对原范围记录，不直接重复上传。')
    uploadReceipt.value={
      asset_id:asset.id,
      scope:asset.scope,
      event_id:asset.source_event_id,
      phase:'complete',
      name:attempt.name
    }
  } catch (e) {
    if(!fresh())return
    const receipt=savedReceipt(e.details,attempt.scope)
    if(receipt){
      uploadReceipt.value={...receipt,name:attempt.name};
      uploadError.value=e.message
    }
    else if(requestRefused(e))uploadError.value=e.message
    else {
      uploadReview.value=attempt;
      uploadError.value='未取得属于本次上传的有效确认，结果未知。接口信息：'+e.message
    }
  }
  finally {
    if (fresh()) uploading.value = false
  }
}
function viewUploaded() {
  if(!uploadReceipt.value||uploading.value)return
  return router.push({
    name:'media',
    query:{
      return_to:route.query.return_to,
      scene:uploadReceipt.value.scope,
      id:uploadReceipt.value.asset_id,
      page:1
    }
  })
}
async function inspectUploadScope() {
  if(!uploadReview.value||uploading.value)return
  if(!window.confirm('关闭本次结果未确认的表单并查看原范围记录？请求不会因此取消，也不会重新上传。'))return
  const scope=uploadReview.value.scope
  const target={name:'media',query:{return_to:route.query.return_to,scene:scope,page:1}}
  const sameLocation=router.resolve(target).fullPath===route.fullPath
  uploadOpen.value=false;
  uploadGuard();
  uploadForm.value={scope,description:'',tags:'',file:null}
  if(sameLocation)refresh()
  else await router.push(target)
}
watch(() => [
  listScope.value,
  route.query.q,
  route.query.kind,
  route.query.enabled,
  route.query.purpose,
  route.query.page
], () => {
  assets.value = [];
  total.value = 0;
  loaded.value = false;
  readAt.value = null
  scope.value = listScope.value;
  query.value = route.query.q || '';
  kind.value = route.query.kind || 'all';
  enabled.value = route.query.enabled || 'all';
  purpose.value = route.query.purpose || 'all';
  load()
}, { immediate: true })
watch(listScope, () => {
  palette.value = [];
  paletteReadAt.value = null;
  paletteLimit.value = null;
  paletteLoaded.value = false;
  loadPalette()
}, { immediate: true })
watch(detailKey, () => {
  saveGuard();
  detailGuard();
  saving.value=false;
  saveError.value='';
  detailMessage.value='';
  readbackPending.value=false;
  savedNotice.value=null;
  saveReview.value=null;
  conflicts.clear('media');
  loadDetail()
}, { immediate:true,flush:'sync' })
watch(() => route.fullPath, () => {
  uploadGuard()
  uploadOpen.value=false;
  uploading.value=false;
  uploadError.value='';
  uploadReceipt.value=null;
  uploadReview.value=null
  uploadForm.value = { scope: listScope.value, description: '', tags: '', file: null }
}, {flush:'sync'})
</script>

<template>
  <div class="page-stack">
    <PageHeader title="媒体与素材" description="消息媒体、工具产物与运营图片。登记、预览和模型实际读取是不同的事实。">
      <v-btn variant="outlined" :loading="loading || paletteLoading" @click="refresh">刷新列表与目录</v-btn>
      <v-btn color="primary" :disabled="saving||uploading" @click="openUpload">上传图片素材</v-btn>
    </PageHeader>
    <v-alert v-if="error" type="error" variant="tonal">
      {{ error }}<span v-if="readAt"> · 上次读取 {{ fmtTime(readAt) }}</span>
    </v-alert>
    <v-card class="pa-4">
      <v-form class="filters" @submit.prevent="filter">
        <ScopeSelect v-model="scope" :include-global="true" :clearable="false" />
        <v-text-field v-model="query" label="描述或标签" hide-details clearable />
        <v-select
          v-model="kind"
          :items="[{title:'全部来源',value:'all'},{title:'运营素材',value:'curated'},{title:'消息与工具媒体',value:'chat'}]"
          label="来源"
          hide-details
        />
        <v-select
          v-model="enabled"
          :items="[{title:'全部状态',value:'all'},{title:'已启用',value:'enabled'},{title:'已停用',value:'disabled'}]"
          label="状态"
          hide-details
        />
        <v-select
          v-model="purpose"
          :items="[{title:'全部用途',value:'all'},{title:'反应表情',value:'sticker'},{title:'人物参考',value:'character_reference'},{title:'一般媒体',value:'media'}]"
          label="用途"
          hide-details
        />
        <v-btn type="submit" color="primary" :disabled="loading">查询</v-btn>
      </v-form>
    </v-card>
    <CharacterReferencesPanel ref="referenceEditor" />
    <v-expansion-panels>
      <v-expansion-panel title="固定表情目录">
        <v-expansion-panel-text>
          <div class="list-summary mb-3">
            <p class="muted">当前配置最多 {{ paletteLimit ?? '未读取' }} 项，按运营顺序提供，独立于下方列表筛选。目录不证明某轮实际装配或使用。</p>
            <v-btn variant="text" size="small" :loading="paletteLoading" @click="loadPalette">刷新目录</v-btn>
          </div>
          <v-progress-linear v-if="paletteLoading" indeterminate aria-label="正在读取固定目录" />
          <v-alert v-if="paletteError" type="error" variant="tonal" class="mb-3">
            {{ paletteError }}<span v-if="paletteReadAt"> · 下方保留上次目录</span>
          </v-alert>
          <p v-if="paletteReadAt" class="muted mb-3">目录读取于 {{ fmtTime(paletteReadAt) }}</p>
          <p
            v-if="paletteLoaded && !paletteLoading && !paletteError && !palette.length"
            class="muted"
          >当前范围尚无固定目录素材。</p>
          <div class="palette-list">
            <RouterLink
              v-for="asset in palette"
              :key="asset.id"
              :to="toAsset(asset)"
              class="palette-item"
            >
              <div class="palette-preview">
                <MediaPreview :asset="asset" :scene-id="listScope" />
              </div>
              <span class="clamp-2">
                {{ asset.palette_order }} · {{ asset.description || asset.id }}
              </span>
            </RouterLink>
          </div>
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>
    <div v-if="loaded" class="list-summary">
      <span>{{ total }} 项媒体</span>
      <span v-if="readAt" class="muted">读取于 {{ fmtTime(readAt) }}</span>
    </div>
    <v-progress-linear v-if="loading" indeterminate aria-label="正在读取素材" />
    <div class="media-grid">
      <v-card v-for="asset in assets" :key="asset.id" tag="article">
        <RouterLink :to="toAsset(asset)" class="asset-link">
          <div class="asset-preview"><MediaPreview :asset="asset" :scene-id="listScope" /></div>
          <div class="asset-copy">
            <strong class="clamp-2">
              {{ asset.description || (asset.curated ? '运营素材' : '已登记媒体') }}
            </strong>
            <p class="muted asset-scope">{{ asset.scope }} · {{ asset.mime_type || '图片类型待读取' }}</p>
            <div class="chips">
              <v-chip size="small">{{ purposeLabels[asset.purpose] || '一般媒体' }}</v-chip>
              <v-chip size="small" :color="asset.enabled ? 'success' : 'default'">
                {{ asset.enabled ? '已启用' : '已停用' }}
              </v-chip>
              <v-chip v-if="asset.palette_order != null" size="small" color="primary">目录 {{ asset.palette_order }}
              </v-chip>
              <v-chip v-if="asset.in_current_limit" size="small" color="success">当前目录限额内</v-chip>
              <v-chip v-else-if="asset.in_initial_catalog" size="small">有序号，未进入当前目录</v-chip>
              <v-chip
                v-if="asset.curated && !asset.description_sufficient"
                size="small"
                color="warning"
              >缺描述/标签</v-chip>
            </div>
            <p class="asset-tags clamp-2">
              {{ asset.tags.slice(0,3).join(' · ') }}<span v-if="asset.tags.length > 3"> · +{{ asset.tags.length - 3 }}</span>
            </p>
          </div>
        </RouterLink>
      </v-card>
    </div>
    <v-card v-if="loaded && !loading && !error && !assets.length" class="pa-8 text-center muted">当前范围没有匹配素材</v-card>
    <v-pagination
      v-if="total > 48"
      :model-value="page"
      :length="Math.ceil(total/48)"
      :total-visible="5"
      @update:model-value="value => router.push({name:'media',query:{...route.query,page:value}})"
    />
    <v-dialog
      :model-value="!!route.query.id"
      max-width="920"
      scrollable
      :persistent="saving"
      @update:model-value="value => !value && closeDetail()"
    >
      <v-card>
        <v-card-title class="dialog-title">媒体详情<div>
            <v-btn
              variant="text"
              :loading="detailLoading"
              :disabled="saving"
              @click="refreshDetail"
            >刷新详情</v-btn>
            <v-btn variant="text" :disabled="saving" @click="closeDetail">关闭</v-btn>
          </div>
        </v-card-title>
        <v-card-text>
          <v-progress-linear v-if="detailLoading" indeterminate />
          <v-alert v-if="detailError" type="error" variant="tonal" class="mb-4">
            {{ detailError }}<span v-if="detailReadAt"> · 保留上次读取内容及当前表单</span>
          </v-alert>
          <v-alert v-if="saveError" type="error" variant="tonal" class="mb-4">
            {{ saveError }}
          </v-alert>
          <v-alert v-if="detailMessage" type="info" variant="tonal" class="mb-4">
            {{ detailMessage }}
          </v-alert>
          <v-alert v-if="readbackPending" type="warning" variant="tonal" class="mb-4">素材已经入库，但当前表单尚未读回新值，暂不再次提交。请重读保存值；重读不重新登记事件。<v-btn
              variant="text"
              :disabled="saving"
              :loading="detailLoading"
              @click="refreshDetail"
            >重读保存值</v-btn>
          </v-alert>
          <v-alert v-if="saveReview" type="warning" variant="tonal" class="mb-4">
            <p>向 {{ saveReview.scope }} 保存 {{ saveReview.baseline.id }} 的请求结果未确认，当前草稿不再次提交。读到当前值不证明旧请求成功、失败或已停止；相同值也不作为本次保存回执。</p>
            <div class="chips mt-3">
              <v-btn
                variant="outlined"
                :disabled="saving"
                :loading="detailLoading"
                @click="refreshDetail"
              >读取当前保存值</v-btn>
              <v-btn
                variant="text"
                :disabled="saving||detailLoading||!saveReview.current"
                @click="adoptReviewedValues"
              >放弃旧草稿，采用当前值重新编辑</v-btn>
            </div>
            <ResourceViewer
              :content="{baseline:saveReview.baseline,submitted:{description:saveReview.description,tags:saveReview.tags,enabled:saveReview.enabled,palette_order:saveReview.palette_order}}"
              title="原请求与草稿"
            />
            <template v-if="saveReview.current">
              <p class="mt-3">当前值读取于 {{ fmtTime(saveReview.readAt) }}</p>
              <ResourceViewer
                :content="assetBaseline(saveReview.current)"
                title="本次读取的当前值（不是原请求回执）"
              />
            </template>
          </v-alert>
          <p v-if="savedNotice" class="muted mb-3">原 {{ savedNotice.phase==='event'?'事件登记':'保存值读取' }} 阶段未正常结束；取得保存值不等于该阶段已恢复。<EntityLink
              v-if="savedNotice.event_id"
              type="event"
              :id="savedNotice.event_id"
              :scene-id="savedNotice.scope"
              label="核对本次变更事件（可能尚不可读）"
            />
          </p>
          <ConfigConflictBanner
            :conflict="currentConflict?.problem"
            :current="currentConflict?.snapshot"
            :path-label="currentConflict?.problem.path.join(' → ')"
            :read-at="currentConflict?.readAt"
            :read-error="currentConflict?.readError"
            :busy="saving||detailLoading||readbackPending"
            @keep="resolveConflict(true)"
            @take="resolveConflict(false)"
            @reload="refreshDetail"
          >
            <template #current>
              <ResourceViewer :content="currentConflict?.snapshot" title="本次读取的素材保存值" />
            </template>
          </ConfigConflictBanner>
          <p v-if="detailReadAt" class="muted mb-3">详情读取于 {{ fmtTime(detailReadAt) }}</p>
          <template v-if="selected">
            <p class="entity-id mb-3">{{ selected.id }}</p>
            <div class="detail-image">
              <MediaPreview
                :asset="selected"
                :scene-id="scalar(route.query.scene) || 'global-safe'"
                interactive
              />
            </div>
            <div class="detail-meta">
              <v-chip>{{ selected.curated ? '运营素材' : '消息或工具媒体 · 只读' }}</v-chip>
              <span>{{ selected.scope }}</span>
              <span>{{ selected.mime_type || '图片类型待读取' }}</span>
              <span>{{ fmtTime(selected.created_at) }}</span>
              <a :href="preview(selected)" target="_blank" rel="noopener">打开原媒体</a>
            </div>
            <p class="muted mb-4">音视频须手动播放；此处预览不代表模型已看、已听或已转写，也不改变原资料的阅读范围。</p>
            <div v-if="selected.purpose==='character_reference'" class="mb-4">
              <p class="muted mb-2">人物参考用于按需辨认，独立于反应表情。先保存素材标签与状态，再选择人物与服装。</p>
              <v-btn
                variant="outlined"
                :disabled="dirty||saving||detailLoading||!!detailError||readbackPending||!!saveReview||!!currentConflict||!selected.curated||!selected.enabled||selected.palette_order!==null||!referenceEditor?.canAdd"
                @click="addReference"
              >加入人物参考草稿</v-btn>
            </div>
            <p v-if="!purposeValid" class="text-error mb-3" role="alert">人物参考请移除“表情包”标签，并将固定目录顺序留空。</p>
            <p v-if="changedSinceEdit&&!currentConflict" class="muted mb-4">本次读取的保存值已有变化，编辑草稿和原基线仍保留；保存只合并实际编辑的字段，不按刷新后的记录覆盖整份素材。</p>
            <v-form
              v-if="draft"
              :disabled="saving||detailLoading||readbackPending||!!saveReview"
              class="edit-form"
              @submit.prevent="save"
            >
              <v-textarea
                v-model="draft.description"
                label="完整描述"
                maxlength="2000"
                auto-grow
                rows="3"
              />
              <v-text-field v-model="draft.tagsText" label="标签（空格分隔）" />
              <v-text-field
                :model-value="draft.palette_order"
                type="number"
                min="0"
                step="1"
                clearable
                label="固定目录顺序"
                hint="非负整数，越小越靠前；留空不列入目录。目录数量由运行配置限制。"
                persistent-hint
                @update:model-value="setPaletteOrder"
              />
              <v-switch v-model="draft.enabled" label="启用素材" color="primary" hide-details />
              <p v-if="!paletteValid" class="text-error" role="alert">目录顺序只能填写非负整数或留空。</p>
              <v-btn
                type="submit"
                color="primary"
                :loading="saving"
                :disabled="!dirty||!paletteValid||!purposeValid||saving||detailLoading||!!detailError||readbackPending||!!saveReview||!!currentConflict||!selected.curated"
              >保存素材与目录</v-btn>
            </v-form>
            <template v-else>
              <p class="full-text">{{ selected.description || '没有描述' }}</p>
              <div class="chips">
                <v-chip v-for="tag in selected.tags" :key="tag" size="small">{{ tag }}</v-chip>
              </div>
              <p class="muted my-4">非运营媒体保留其原始登记，在此只读；不能借此改写来源、描述或启用状态。</p>
            </template>
            <v-divider class="my-4" />
            <h3 class="mb-2">来源记录</h3>
            <EntityLink
              v-if="selected.source_event_id"
              type="event"
              :id="selected.source_event_id"
              :scene-id="selected.scope"
            />
            <p v-else class="muted">未记录来源事件</p>
          </template>
        </v-card-text>
      </v-card>
    </v-dialog>
    <v-dialog
      :model-value="uploadOpen"
      max-width="640"
      scrollable
      :persistent="uploading"
      @update:model-value="value => !value && closeUpload()"
    >
      <v-card>
        <v-card-title class="dialog-title">上传运营图片<v-btn variant="text" :disabled="uploading" @click="closeUpload">关闭</v-btn>
        </v-card-title>
        <v-card-text>
          <v-alert v-if="uploadError" type="error" variant="tonal" class="mb-4">
            {{ uploadError }}
          </v-alert>
          <v-alert
            v-if="uploadReceipt"
            :type="uploadReceipt.phase==='complete'?'success':'warning'"
            variant="tonal"
            class="mb-4"
          >
            <p>
              {{ uploadReceipt.name }} 已登记为 {{ uploadReceipt.asset_id }}，范围 {{ uploadReceipt.scope }}。</p>
            <p class="mt-2">
              {{ uploadReceipt.phase==='complete'?'这不是向群上传或发送的回执。可进入详情查看原记录并设置固定目录。':'图片已入库，后续阶段尚未正常结束。请核对原素材，不要重新上传同一请求。' }}
            </p>
            <div class="chips mt-3">
              <v-btn variant="outlined" :disabled="uploading" @click="viewUploaded">查看已登记素材</v-btn>
              <v-btn
                v-if="uploadReceipt.phase==='complete'"
                variant="text"
                :disabled="uploading"
                @click="openUpload"
              >再登记一张图片</v-btn>
            </div>
            <EntityLink
              v-if="uploadReceipt.event_id"
              type="event"
              :id="uploadReceipt.event_id"
              :scene-id="uploadReceipt.scope"
              label="核对登记事件"
            />
          </v-alert>
          <v-alert v-if="uploadReview" type="warning" variant="tonal" class="mb-4">
            <p>此次 {{ uploadReview.name }}（{{ uploadReview.size }} 字节）向 {{ uploadReview.scope }} 的上传结果未确认，当前表单不再次发送。</p>
            <v-btn
              class="mt-3"
              variant="outlined"
              :disabled="uploading"
              @click="inspectUploadScope"
            >查看提交范围的素材列表</v-btn>
          </v-alert>
          <v-form
            v-if="!uploadReceipt"
            class="edit-form"
            :disabled="uploading||!!uploadReview"
            @submit.prevent="upload"
          >
            <p class="muted">仅支持 PNG、JPEG、WEBP、GIF；{{ uploadMaxBytes == null ? '大小上限按当前运行配置' : `大小上限 ${uploadMaxBytes.toLocaleString()} 字节` }}。公共素材可在所有场景使用。此处不上传音视频，也不直接向群发送。</p>
            <p class="muted">请求发出后，离开页面不等于取消服务器处理；结果未确认时请回原范围刷新，不要重复上传。</p>
            <ScopeSelect
              v-model="uploadForm.scope"
              :include-global="true"
              :clearable="false"
              :disabled="uploading||!!uploadReview"
            />
            <v-file-input
              v-model="uploadForm.file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              label="图片文件"
              show-size
              required
            />
            <v-textarea
              v-model="uploadForm.description"
              label="描述用途或情绪"
              maxlength="2000"
              rows="3"
            />
            <v-text-field
              v-model="uploadForm.tags"
              label="标签（空格分隔）"
              hint="人物常服使用 人物参考；反应图使用 表情包。两种用途分别登记。"
              persistent-hint
            />
            <p>将保存到：<strong>{{ uploadForm.scope || '请选择范围' }}</strong></p>
            <v-btn
              type="submit"
              color="primary"
              :loading="uploading"
              :disabled="uploading||!!uploadReview||!uploadForm.file||!uploadForm.scope"
            >上传到所选范围</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </v-dialog>
  </div>
</template>
<style scoped>
.filters{display:grid;grid-template-columns:minmax(170px,1.2fr) minmax(160px,1.5fr) repeat(3,minmax(110px,.7fr)) auto;gap:12px;align-items:center}
.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px}
.asset-link{display:block;color:inherit;text-decoration:none;height:100%}
.asset-preview{aspect-ratio:1;background:#f4f6f9;display:flex;align-items:center;justify-content:center;padding:12px}
.asset-copy{padding:14px;min-width:0;display:grid;gap:8px}
.asset-copy strong{line-height:1.5;min-height:3em;overflow-wrap:anywhere}
.asset-scope{font-size:12px;overflow-wrap:anywhere}
.asset-tags{font-size:12px;color:#64748b;min-height:1.5em}
.chips,.detail-meta{display:flex;gap:8px;flex-wrap:wrap}
.list-summary,.dialog-title{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}
.palette-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.palette-item{display:flex;gap:10px;align-items:center;min-width:0;text-decoration:none;color:inherit}
.palette-preview{width:48px;height:48px;flex:none;background:#f4f6f9}
.detail-image{height:clamp(220px,42vh,450px);background:#f4f6f9;display:flex;margin-bottom:16px;min-width:0}
.detail-meta{font-size:13px;align-items:center;margin-bottom:20px}
.edit-form{display:grid;gap:12px}
.edit-form>.v-btn{justify-self:start}
.full-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7}
.asset-link:focus-visible,.palette-item:focus-visible{outline:3px solid #2563eb;outline-offset:-3px}
@media(max-width:1000px){
  .filters{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:500px){
  .filters{grid-template-columns:minmax(0,1fr)}
  .media-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
  .asset-copy{padding:10px}
  .chips{gap:4px}
  .detail-image{height:260px}
}
</style>
