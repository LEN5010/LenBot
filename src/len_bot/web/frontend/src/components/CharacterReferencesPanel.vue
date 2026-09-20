<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, fmtTime } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useConfigConflicts } from '../composables/useConfigConflicts.js'
import { hasConfigDraftChanges, rebaseConfigDraft } from '../lib/configDraft.js'
import ConfigConflictBanner from './ConfigConflictBanner.vue'
import ResourceViewer from './ResourceViewer.vue'
import MediaPreview from './MediaPreview.vue'

const route = useRoute()
const expanded = ref(false), loading = ref(false), saving = ref(false)
const snapshot = ref(null), baseline = ref(null), rows = ref([]), knownAssets = ref({})
const error = ref(''), message = ref(''), readAt = ref(null)
const needsReadback = ref(false), uncertain = ref(null)
const conflicts = useConfigConflicts(), conflict = computed(() => conflicts.entries.references)
const readGuard = useRequestGuard(), saveGuard = useRequestGuard()
const clone = value => JSON.parse(JSON.stringify(value))
const values = () => ({ character_reference_assets: rows.value.map(row => ({...row})) })
const dirty = computed(() => !!baseline.value && hasConfigDraftChanges(baseline.value, values()))
const locked = computed(() => loading.value || saving.value || needsReadback.value || !!uncertain.value || !!conflict.value)
const canAdd = computed(() => !!baseline.value && !locked.value && !error.value && rows.value.length < 40)
const valid = computed(() => rows.value.every(row => /^[a-z][a-z0-9_-]{0,31}$/.test(row.character_key)
  && row.outfit.trim().length > 0 && row.outfit.trim().length <= 80 && row.asset_id.trim().length > 0 && row.asset_id.length <= 200)
  && new Set(rows.value.map(row => JSON.stringify([row.character_key, row.outfit.trim()]))).size === rows.value.length
  && new Set(rows.value.map(row => row.asset_id)).size === rows.value.length)
useUnsavedChanges(computed(() => dirty.value || saving.value || needsReadback.value || !!uncertain.value))

function adopt(current) {
  baseline.value = clone(current.baseline)
  rows.value = clone(current.saved.character_reference_assets)
  needsReadback.value = false
}
async function load({ accept = () => true } = {}) {
  const own = readGuard(), fresh = () => own() && accept()
  loading.value = true; error.value = ''; conflicts.beginRead('references')
  if (uncertain.value) uncertain.value.current = null
  try {
    const current = await api('/api/settings/character-references')
    if (!fresh()) return
    if (!Array.isArray(current.saved?.character_reference_assets) || !Array.isArray(current.baseline?.character_reference_assets)
      || !Array.isArray(current.effective?.character_reference_assets) || !Array.isArray(current.configured)) {
      throw new Error('未取得完整的参考绑定配置，请重新读取。')
    }
    if (uncertain.value) uncertain.value.current = current
    else if (needsReadback.value || !conflicts.capture('references', current) && !dirty.value) adopt(current)
    snapshot.value = current; readAt.value = Date.now() / 1000
    for (const item of current.configured) knownAssets.value[item.asset_id] = item.asset
  } catch (problem) {
    if (fresh()) { error.value = problem.message; conflicts.readFailed('references', problem) }
  } finally { if (fresh()) loading.value = false }
}
function addAsset(asset = null) {
  if (!canAdd.value) return false
  if (asset && rows.value.some(row => row.asset_id === asset.id)) {
    expanded.value = true; message.value = '该素材已在绑定草稿中，请编辑已有条目。'; return true
  }
  if (asset) knownAssets.value[asset.id] = asset
  rows.value.push({character_key:'', outfit:'', asset_id:asset?.id || ''})
  expanded.value = true; message.value = '请填写人物键与服装，再单独保存参考绑定。当前只是草稿。'
  return true
}
function resolveConflict(keep) {
  const current = conflict.value?.snapshot
  if (saving.value || loading.value || !current) return
  if (!keep && !window.confirm('放弃未保存的人物参考绑定，采用本次读取的保存值？')) return
  const next = keep ? rebaseConfigDraft(baseline.value, values(), current.saved) : null
  adopt(current)
  if (keep) rows.value = clone(next.character_reference_assets)
  conflicts.clear('references'); error.value = ''
  message.value = keep ? '绑定列表按整项保留草稿，请核对现有绑定后另行保存。' : '已采用当前保存值，没有提交修改。'
}
function adoptUncertain() {
  if (saving.value || loading.value || !uncertain.value?.current) return
  if (!window.confirm('放弃旧请求的草稿，采用当前保存值重新编辑？这不确认旧请求结果，也不会重新发送旧请求。')) return
  adopt(uncertain.value.current); uncertain.value = null; error.value = ''
  message.value = '已采用当前保存值；旧请求的结果仍未确认。'
}
async function save() {
  if (locked.value || error.value || !dirty.value || !valid.value) return
  if (!window.confirm('保存人物与服装参考绑定？后续新对话按素材原范围、启用状态和图片预算使用。此操作不上传图片、不发送群消息。')) return
  const fresh = saveGuard(), body = {baseline:clone(baseline.value), values:values()}
  readGuard(); loading.value = false; saving.value = true; error.value = ''; message.value = ''
  try {
    const result = await api('/api/settings/character-references', {method:'POST', body:JSON.stringify(body)})
    if (!fresh()) return
    if (result?.success !== true) throw new Error('未取得属于本次请求的配置保存确认。')
    needsReadback.value = true; message.value = result.message
    await load({accept:fresh})
  } catch (problem) {
    if (!fresh()) return
    if (conflicts.mark('references', problem)) await load({accept:fresh})
    else {
      error.value = problem.message
      if (![400,401,403,422].includes(problem.status)) uncertain.value = {...body, current:null}
    }
  } finally { if (fresh()) saving.value = false }
}
const assetFor = row => knownAssets.value[row.asset_id]
const assetLink = asset => ({name:'media', query:{...route.query, list_scene:route.query.list_scene || route.query.scene || 'global-safe', scene:asset.scope, id:asset.id}})
onMounted(load)
defineExpose({addAsset, canAdd})
</script>

<template>
  <v-card class="pa-4" id="character-references">
    <div class="reference-heading">
      <div><h2 class="text-h6">人物与服装参考</h2><p class="muted">{{ snapshot ? `已保存 ${snapshot.configured.length} 项` : '尚未读取' }} · 平时提供定位，辨认时按需读图，独立于固定表情目录。</p></div>
      <v-btn variant="text" :aria-expanded="expanded" aria-controls="character-reference-form" @click="expanded=!expanded">{{ expanded ? '收起' : '管理绑定' }}{{ dirty ? ' · 有未保存修改' : '' }}</v-btn>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" class="mt-3">{{ error }}</v-alert>
    <div v-show="expanded" id="character-reference-form" class="reference-form mt-4">
      <p class="muted">先上传运营图片，设置“人物参考”标签并将固定目录顺序留空。在素材详情选择“加入人物参考草稿”，或在此填写已有资产 ID。此处只保存绑定，素材说明与启用状态在详情编辑。</p>
      <p class="muted">绑定列表属于全局配置；每张图仍只在原素材范围内可用，global-safe 可用于所有场景。人物键与服装组合、资产 ID 各自唯一，最多 40 项。</p>
      <div class="reference-actions"><v-btn variant="outlined" :loading="loading" :disabled="saving" @click="load()">读取当前绑定</v-btn><v-btn variant="text" :disabled="!canAdd" @click="addAsset()">添加空白绑定</v-btn><span v-if="readAt" class="muted">读取于 {{ fmtTime(readAt) }}</span></div>
      <v-alert v-if="snapshot && !snapshot.media_enabled" type="warning" variant="tonal">当前媒体功能关闭。保存绑定不会开启媒体功能，模型暂不获得人物参考目录。</v-alert>
      <v-alert v-if="message" type="info" variant="tonal">{{ message }}</v-alert>
      <v-alert v-if="needsReadback" type="warning" variant="tonal">配置保存已确认，当前表单尚未读回新值。请读取当前绑定；读取不会再次保存。</v-alert>
      <v-alert v-if="uncertain" type="warning" variant="tonal">
        <p>本次保存结果未确认，旧草稿不会直接重发。读取当前值只能显示现状，不能确认旧请求成功或失败。</p>
        <ResourceViewer :content="{baseline:uncertain.baseline,submitted:uncertain.values}" title="未确认的保存请求" />
        <template v-if="uncertain.current"><ResourceViewer :content="uncertain.current.saved" title="本次读取的当前值" /><v-btn variant="text" :disabled="loading||saving" @click="adoptUncertain">采用当前值重新编辑</v-btn></template>
      </v-alert>
      <ConfigConflictBanner :conflict="conflict?.problem" :current="conflict?.snapshot" :path-label="conflict?.problem.path.join(' → ')" :read-at="conflict?.readAt" :read-error="conflict?.readError" :busy="loading||saving" @keep="resolveConflict(true)" @take="resolveConflict(false)" @reload="load()">
        <template #current><ResourceViewer :content="conflict?.snapshot?.saved" title="当前已保存的绑定列表" /></template>
      </ConfigConflictBanner>
      <v-form v-if="baseline" :disabled="locked" class="reference-form" @submit.prevent="save">
        <v-card v-for="(row,index) in rows" :key="index" variant="outlined" class="pa-3">
          <div class="reference-fields">
            <v-text-field v-model="row.character_key" label="人物键" hint="小写字母开头，可含数字、短横线与下划线" persistent-hint maxlength="32" />
            <v-text-field v-model="row.outfit" label="服装名称／版本" maxlength="80" />
            <v-btn variant="text" color="error" :disabled="locked" :aria-label="`移除第 ${index+1} 项绑定`" @click="rows.splice(index,1)">移除</v-btn>
          </div>
          <v-text-field v-model="row.asset_id" label="已登记图片资产 ID" maxlength="200" hide-details />
          <div v-if="assetFor(row)" class="reference-asset mt-3">
            <div class="reference-preview"><MediaPreview :asset="assetFor(row)" :scene-id="assetFor(row).scope" /></div>
            <div><RouterLink :to="assetLink(assetFor(row))">{{ assetFor(row).description || row.asset_id }} · 查看素材</RouterLink><p class="muted">{{ assetFor(row).scope }} · {{ assetFor(row).enabled ? '已启用' : '已停用' }} · 此处预览不代表模型已读</p></div>
          </div>
        </v-card>
        <p v-if="!rows.length" class="muted">当前没有参考绑定。参考图片入库后，可在这里逐张选择人物与服装。</p>
        <p v-if="!valid" class="text-error" role="alert">请补齐合法人物键、服装和资产 ID，并移除重复人物／服装或重复资产。</p>
        <v-btn type="submit" color="primary" :loading="saving" :disabled="locked||!!error||!dirty||!valid">保存参考绑定</v-btn>
      </v-form>
      <template v-if="snapshot">
        <div v-for="item in snapshot.configured.filter(item=>item.issue)" :key="item.asset_id" class="text-warning">已保存的 {{ item.character_key }} / {{ item.outfit }}：{{ item.issue }}。当前目录不会提供此项。</div>
        <ResourceViewer :content="snapshot.effective" title="当前运行绑定快照（不证明某轮实际装配或像素已读）" />
      </template>
    </div>
  </v-card>
</template>

<style scoped>
.reference-heading,.reference-actions{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.reference-actions{justify-content:flex-start}.reference-form{display:grid;gap:16px}.reference-form>.v-btn{justify-self:start}.reference-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:start}.reference-asset{display:flex;gap:12px;align-items:center;overflow-wrap:anywhere}.reference-asset>div:last-child{min-width:0}.reference-preview{width:60px;height:60px;flex:none;background:#f4f6f9}@media(max-width:600px){.reference-fields{grid-template-columns:minmax(0,1fr)}.reference-fields>.v-btn{justify-self:end}.reference-heading>.v-btn{margin-left:auto}}
</style>
