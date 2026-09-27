<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import HostMemorySettings from '../components/HostMemorySettings.vue'
import HostMemoryIngest from '../components/HostMemoryIngest.vue'

const route = useRoute(), router = useRouter()
const state = ref(null), scene = ref(''), scope = ref('scene'), directory = ref('')
const nodes = ref([]), hasMore = ref(false), selected = ref(null), content = ref(''), original = ref('')
const editPath = ref(''), reason = ref(''), staleFile = ref(false)
const searchQuery = ref(''), hits = ref([]), history = ref([]), lastResult = ref(null)
const searched = ref(false)
const tab = ref('browse')
const settingsDirty = ref(false)
const stateLoading = ref(false), browsing = ref(false), reading = ref(false), searching = ref(false)
const writing = ref(false), deleting = ref(false), historyLoading = ref(false)
const stateError = ref(''), browseError = ref(''), fileError = ref(''), searchError = ref('')
const writeError = ref(''), deleteError = ref(''), historyError = ref('')
let selectionEpoch = 0, editorEpoch = 0
const selection = () => `${scene.value}\u0000${scope.value}\u0000${selectionEpoch}`
const fileSelection = () => `${selection()}\u0000${editorEpoch}`
const beginState = useRequestGuard(), beginBrowse = useRequestGuard(selection)
const beginFile = useRequestGuard(fileSelection), beginSearch = useRequestGuard(selection)
const beginHistory = useRequestGuard(fileSelection), beginWrite = useRequestGuard(fileSelection)
const beginDelete = useRequestGuard(fileSelection)
const enabled = computed(() => state.value?.enabled === true)
const can = action => enabled.value && state.value.actions.includes(action)
const writable = computed(() => can('write') && (scope.value === 'scene' || state.value.public_writable))
const deletable = computed(() => scope.value === 'scene' && can('delete') && selected.value !== null && !staleFile.value)
const dirty = computed(() => selected.value === null
  ? editPath.value !== '' || content.value !== '' || reason.value !== ''
  : editPath.value !== selected.value || content.value !== original.value || reason.value !== '')
const pageDirty = computed(() => dirty.value || settingsDirty.value)
const options = computed(() => state.value?.scenes.map(item => ({
  title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene,
})) || [])
const scopes = computed(() => [
  { title: '此场景', value: 'scene' },
  ...(state.value?.public_readable ? [{ title: '公共', value: 'public' }] : []),
])
useUnsavedChanges(pageDirty)
onBeforeRouteUpdate(() => !dirty.value || window.confirm('有未保存的记忆正文或修改原因。放弃并切换场景？'))
function query(path, values) { return `${path}?${new URLSearchParams(values)}` }
function parentOf(path) { return path.split('/').slice(0, -1).join('/') }
function displayTime(value) { return new Date(value * 1000).toISOString() }
function resetFile() {
  ++editorEpoch
  selected.value = null; editPath.value = ''; content.value = ''; original.value = ''; reason.value = ''
  staleFile.value = false; history.value = []; fileError.value = ''; historyError.value = ''
  historyLoading.value = false
}
function resetSelection() {
  ++selectionEpoch
  directory.value = ''; nodes.value = []; hasMore.value = false
  hits.value = []; searched.value = false; lastResult.value = null; browseError.value = ''; searchError.value = ''
  writeError.value = ''; deleteError.value = ''; browsing.value = false; reading.value = false
  searching.value = false; historyLoading.value = false; resetFile()
}
async function browse(more = false) {
  if (!can('browse') || !scene.value || (more && (!hasMore.value || browsing.value))) return
  const target = scene.value, access = scope.value, path = directory.value
  const offset = more ? nodes.value.length : 0
  const fresh = beginBrowse()
  browsing.value = true
  try {
    const result = await api(query('/api/host/memory/browse', { scene: target, path, scope: access, offset: String(offset), limit: '50' }))
    if (!fresh() || path !== directory.value) return
    nodes.value = more ? [...nodes.value, ...result.nodes] : result.nodes
    hasMore.value = result.has_more
    browseError.value = ''
  } catch (error) { if (fresh()) browseError.value = error.message }
  finally { if (fresh()) browsing.value = false }
}
function openDirectory(path) {
  if (dirty.value && !window.confirm('放弃未保存的记忆草稿并打开目录？')) return
  directory.value = path; nodes.value = []; hasMore.value = false; resetFile()
  browseError.value = ''; browse()
}
function changeScope(value) {
  if (value === scope.value) return
  if (value === 'public' && !state.value?.public_readable) return
  if (dirty.value && !window.confirm('放弃未保存的记忆草稿并切换范围？')) return
  scope.value = value; resetSelection(); browse()
}
function changeScene(value, fromRoute = false) {
  if (value === scene.value) return
  if (!fromRoute && dirty.value && !window.confirm('放弃未保存的记忆草稿并切换场景？')) return
  scene.value = value; resetSelection()
  if (!fromRoute) router.replace({ name: 'host-memory', query: { scene: value } })
  browse()
}
async function readFile(path, access = scope.value) {
  if (!can('read')) return
  if (access === 'public' && !state.value.public_readable) return
  if (dirty.value && !window.confirm('放弃未保存的记忆草稿并读取另一文件？')) return
  if (access !== scope.value) { scope.value = access; resetSelection() }
  ++editorEpoch
  historyLoading.value = false
  const target = scene.value, actualScope = scope.value, fresh = beginFile()
  reading.value = true; fileError.value = ''
  try {
    const result = await api(query('/api/host/memory/read', { scene: target, path, scope: actualScope }))
    if (!fresh()) return
    selected.value = result.path; editPath.value = result.path
    content.value = result.content; original.value = result.content; reason.value = ''
    staleFile.value = false; history.value = []; historyError.value = ''; historyLoading.value = false
    directory.value = parentOf(result.path); nodes.value = []; hasMore.value = false
    if (can('browse')) browse()
  } catch (error) { if (fresh()) fileError.value = error.message }
  finally { if (fresh()) reading.value = false }
}
function createFile() {
  if (!writable.value) return
  if (dirty.value && !window.confirm('放弃当前未保存草稿并新建文件？')) return
  resetFile(); tab.value = 'browse'
}
async function search() {
  if (!can('search') || searching.value) return
  const target = scene.value, text = searchQuery.value, fresh = beginSearch()
  hits.value = []; searched.value = true
  searching.value = true; searchError.value = ''
  try {
    const result = await api('/api/host/memory/search', { method: 'POST',
      body: JSON.stringify({ scene: target, query: text, limit: 10 }) })
    if (!fresh() || text !== searchQuery.value) return
    hits.value = result.hits
  } catch (error) { if (fresh()) searchError.value = error.message }
  finally { if (fresh()) searching.value = false }
}
async function readHistory() {
  if (!can('history') || scope.value !== 'scene' || selected.value === null) return
  const path = selected.value, target = scene.value, fresh = beginHistory()
  historyLoading.value = true; historyError.value = ''
  try {
    const result = await api(query('/api/host/memory/history', { scene: target, path }))
    if (fresh() && path === selected.value) history.value = result.changes
  } catch (error) { if (fresh() && path === selected.value) historyError.value = error.message }
  finally { if (fresh() && path === selected.value) historyLoading.value = false }
}
function errorMessage(error, action) {
  return error.status >= 400 && error.status < 500
    ? `${action}未被接受：${error.message}`
    : `${action}结果未确认：${error.message} 草稿已保留；请手动重读核对，不会自动重试。`
}
async function writeFile() {
  if (!writable.value || writing.value || deleting.value || !editPath.value || !reason.value.trim()) return
  const target = scene.value, access = scope.value, path = editPath.value
  const text = content.value, why = reason.value, fresh = beginWrite()
  writing.value = true; writeError.value = ''; lastResult.value = null
  try {
    const result = await api('/api/host/memory/file', { method: 'PUT',
      body: JSON.stringify({ scene: target, path, scope: access, content: text, reason: why }) })
    if (!fresh()) return
    lastResult.value = { kind: 'write', scope: access, path, value: result }
    if (result.action === 'write' || result.content_updated === true) {
      selected.value = path; original.value = typeof result.after === 'string' ? result.after : text
      content.value = original.value; reason.value = ''; staleFile.value = false
      directory.value = parentOf(path); nodes.value = []; hasMore.value = false
      if (can('browse')) browse()
    }
  } catch (error) { if (fresh()) writeError.value = errorMessage(error, '写入') }
  finally { writing.value = false }
}
async function deleteFile(forget) {
  if (!deletable.value || deleting.value || writing.value) return
  if (forget && !can('forget')) return
  const description = forget
    ? '忘记会移除当前路径及可访问的历史版本；不等于清除日志或备份。确定继续？'
    : '普通删除会移除当前文件；若后端保留历史，历史版本仍可能可访问。确定继续？'
  if (!window.confirm(description)) return
  const target = scene.value, path = selected.value, why = reason.value
  if (!why.trim()) { deleteError.value = '请先填写本次删除或忘记的实际原因。'; return }
  const fresh = beginDelete()
  deleting.value = true; deleteError.value = ''; lastResult.value = null
  try {
    const result = await api('/api/host/memory/delete', { method: 'POST',
      body: JSON.stringify({ scene: target, path, reason: why, forget }) })
    if (!fresh() || path !== selected.value) return
    lastResult.value = { kind: forget ? 'forget' : 'delete', scope: 'scene', path, value: result }
    staleFile.value = true
    reason.value = ''
    nodes.value = []; hasMore.value = false
    if (can('browse')) browse()
  } catch (error) { if (fresh()) deleteError.value = errorMessage(error, '删除') }
  finally { deleting.value = false }
}
async function loadState(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃当前未保存的记忆草稿，重新读取后端状态？')) return
  const fresh = beginState()
  stateLoading.value = true
  try {
    const result = await api('/api/host/memory/state')
    if (!fresh()) return
    state.value = result; stateError.value = ''
    if (!result.public_readable && scope.value === 'public') scope.value = 'scene'
    const wanted = route.query.scene
    if (typeof wanted === 'string') scene.value = wanted
    else if (!scene.value && result.scenes.length) scene.value = result.scenes[0].scene
    resetSelection()
    if (scene.value && scene.value !== route.query.scene) router.replace({ name: 'host-memory', query: { scene: scene.value } })
    if (result.enabled && result.actions.includes('browse')) browse()
  } catch (error) { if (fresh()) stateError.value = error.message }
  finally { if (fresh()) stateLoading.value = false }
}
onMounted(() => loadState(false))
watch(() => route.query.scene, value => {
  if (typeof value === 'string' && state.value && value !== scene.value) changeScene(value, true)
})
</script>

<template>
  <div class="page-stack host-memory">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>认识与记忆</h1>
      <p class="muted">按当前宿主实际后端浏览、搜索和维护文件。这里不从聊天自动提炼事实，也不把检索结果当成模型已采用的记忆。</p></div>
      <v-btn variant="outlined" :loading="stateLoading" :disabled="writing||deleting" @click="loadState()">重读后端状态</v-btn></header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'状态读取失败 · 保留上次结果':'状态读取失败'">{{ stateError }}</v-alert>
    <div v-if="stateLoading && !state" class="surface empty-state" role="status">正在读取实际记忆后端…</div>
    <section v-if="state" class="surface"><div class="section-heading"><h2>当前能力</h2><v-chip variant="tonal" :color="enabled?'info':'warning'">{{ enabled?state.backend:'未配置记忆后端' }}</v-chip></div>
      <template v-if="enabled"><p>实际可用操作：{{ state.actions.join('、') || '无' }}。自动回想：{{ state.auto_recall?'已启用':'未启用' }}；回想预算：{{ state.recall_budget_chars===null?'未设置':`${state.recall_budget_chars} 字符` }}。</p>
        <p class="muted">{{ state.public_readable ? (state.public_writable?'公共范围可读写':'公共范围只读') : '当前后端未配置公共读取范围' }}；公共范围永不提供删除。不同后端的索引和历史能力以实际操作列表与操作结果为准。</p></template>
      <p v-else class="muted">当前运行未启用记忆后端，没有可浏览的空树。下方可保存根配置；现有进程不会热启，须按停机流程重启后才可能生效。</p>
    </section>
    <template v-if="enabled && scene">
      <section class="surface"><h2>场景与范围</h2><div class="form-grid"><v-select :model-value="scene" :items="options" label="场景" :disabled="writing||deleting" hide-details="auto" @update:model-value="changeScene" />
        <v-select :model-value="scope" :items="scopes" label="范围" :disabled="writing||deleting" hide-details="auto" @update:model-value="changeScope" /></div></section>
      <v-tabs v-model="tab" aria-label="记忆查看方式"><v-tab value="browse">目录与文件</v-tab><v-tab v-if="can('search')" value="search">搜索</v-tab></v-tabs>
      <section v-if="tab==='browse'" class="surface"><div class="section-heading"><h2>目录</h2><v-btn variant="outlined" :loading="browsing" :disabled="!can('browse')" @click="browse(false)">重读目录</v-btn></div>
        <div class="path-actions"><v-btn variant="text" :disabled="directory===''" @click="openDirectory('')">根目录</v-btn>
          <v-btn variant="text" :disabled="directory===''" @click="openDirectory(parentOf(directory))">上一级</v-btn><span class="muted">{{ directory || '根目录' }}</span></div>
        <v-alert v-if="browseError" type="error" variant="tonal" role="alert">目录读取失败：{{ browseError }}<span v-if="nodes.length"> 下方保留上次读取结果。</span></v-alert>
        <p v-if="browsing && !nodes.length" role="status" class="muted">正在读取当前目录…</p>
        <p v-if="!can('browse')" class="muted">当前后端未提供浏览能力。</p>
        <p v-else-if="!nodes.length && !browsing && !browseError" class="muted">当前目录没有文件或子目录。</p>
        <ul v-if="nodes.length" class="node-list"><li v-for="node in nodes" :key="node.path"><v-btn variant="text" @click="node.is_dir?openDirectory(node.path):readFile(node.path)">{{ node.is_dir?'目录':'文件' }} · {{ node.name }}</v-btn>
          <span v-if="node.access" class="muted">{{ node.access }}</span></li></ul>
        <v-btn v-if="hasMore" variant="outlined" :loading="browsing" @click="browse(true)">读取下一页（最多 50 项）</v-btn>
      </section>
      <section v-if="tab==='search' && can('search')" class="surface"><h2>检索记忆</h2>
        <p class="muted">搜索当前场景及实际可读的公共记忆；上方范围选择只控制目录和文件操作，命中会标明自己的范围。</p>
        <form class="search-form" @submit.prevent="search"><v-text-field v-model="searchQuery" label="搜索原文" :disabled="searching" hide-details="auto" />
          <v-btn type="submit" color="primary" :loading="searching" :disabled="!searchQuery || searching">搜索</v-btn></form>
        <v-alert v-if="searchError" type="error" variant="tonal" role="alert">搜索失败：{{ searchError }}</v-alert>
        <p v-if="!searched" class="muted">输入查询后手动搜索。</p>
        <p v-else-if="!hits.length && !searching && !searchError" class="muted">本次查询没有返回命中；不自动重试或补造结果。</p>
        <ul v-if="hits.length" class="hit-list"><li v-for="hit in hits" :key="`${hit.scope}:${hit.path}`"><v-btn variant="text" @click="readFile(hit.path,hit.scope)">{{ hit.scope==='public'?'公共':'场景' }} · {{ hit.path }}</v-btn>
          <p class="original-text">{{ hit.preview }}</p><p class="muted">总字符：{{ hit.total_chars===null?'未知':hit.total_chars }}<span v-if="hit.score!==null"> · 检索分数 {{ hit.score }}</span></p></li></ul>
      </section>
      <section v-if="can('read') || writable" class="surface"><div class="section-heading"><h2>文件正文</h2><v-btn v-if="writable" variant="outlined" @click="createFile">新建 Markdown 文件</v-btn></div>
        <p v-if="selected">{{ scope==='public'?'公共':'场景' }} · {{ selected }} <span v-if="staleFile" class="muted">（上次读取原文；删除后未重读）</span></p>
        <p v-else class="muted">从目录或搜索结果选择文件；有写入权限时也可新建。路径是后端实际相对路径，不另造编号。</p>
        <v-alert v-if="fileError" type="error" variant="tonal" role="alert">读取正文失败：{{ fileError }}</v-alert>
        <p v-if="reading" role="status" class="muted">正在读取正文…</p>
        <form v-if="writable" class="editor" @submit.prevent="writeFile"><v-text-field v-model="editPath" label="相对 Markdown 路径（例如 notes/topic.md）" :disabled="writing||deleting" hide-details="auto" />
          <v-textarea v-model="content" label="文件完整正文" rows="12" auto-grow :disabled="writing||deleting" hide-details="auto" class="content-editor" />
          <v-textarea v-model="reason" label="本次修改原因" rows="2" auto-grow :disabled="writing||deleting" hide-details="auto" />
          <p v-if="dirty" class="dirty-note" role="status">当前有未保存的正文、路径或原因草稿。</p>
          <div class="form-actions"><v-btn type="submit" color="primary" :loading="writing" :disabled="!editPath || !reason.trim() || writing || deleting">写入文件</v-btn>
            <span class="muted">写入只保存当前正文；向量、语义和概览状态看下方后端实际返回，不统一称“已索引”。</span></div></form>
        <template v-else-if="selected"><p class="muted">此范围当前只读。</p><pre class="original-text">{{ content }}</pre></template>
        <v-alert v-if="writeError" type="error" variant="tonal" role="alert">{{ writeError }}</v-alert>
        <div v-if="selected && scope==='scene'" class="file-actions"><v-btn v-if="can('history')" variant="outlined" :loading="historyLoading" @click="readHistory">查看修改历史</v-btn>
          <v-btn v-if="deletable" variant="outlined" color="error" :loading="deleting" :disabled="!reason.trim()" @click="deleteFile(false)">普通删除</v-btn>
          <v-btn v-if="deletable && can('forget')" variant="outlined" color="error" :loading="deleting" :disabled="!reason.trim()" @click="deleteFile(true)">忘记当前路径与可访问版本</v-btn></div>
        <p v-if="deletable && can('history')" class="muted">普通删除保留本地可访问历史；忘记移除当前路径及其可访问版本，不等于清除日志或备份。</p>
        <v-alert v-if="deleteError" type="error" variant="tonal" role="alert">{{ deleteError }}</v-alert>
        <v-alert v-if="historyError" type="error" variant="tonal" role="alert">历史读取失败：{{ historyError }}</v-alert>
        <div v-if="history.length" class="history"><h3>实际修改历史</h3><ul><li v-for="(item,index) in history" :key="`${item.changed_at}:${index}`">
          <strong>{{ item.action }} · {{ displayTime(item.changed_at) }}</strong><p class="original-text">{{ item.reason }}</p>
          <details><summary>查看改动前后原文</summary><h4>改动前</h4><pre>{{ item.before===null?'无':item.before }}</pre><h4>改动后</h4><pre>{{ item.after===null?'无':item.after }}</pre></details></li></ul></div>
      </section>
      <section v-if="lastResult" class="surface"><h2>后端实际操作结果</h2><p class="muted">{{ lastResult.scope==='public'?'公共':'场景' }} · {{ lastResult.path }} · {{ lastResult.kind }}。下方状态逐字段保留；返回写入不代表所有索引阶段都已成功。</p>
        <pre>{{ JSON.stringify(lastResult.value,null,2) }}</pre></section>
    </template>
    <HostMemoryIngest :scene="scene" />
    <HostMemorySettings @dirty="value=>settingsDirty=value" />
  </div>
</template>

<style scoped>
.host-memory{max-width:1200px;margin-inline:auto}.page-intro,.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 460px}.page-intro h1{margin:0 0 10px}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}.surface h3{font-size:15px;margin:18px 0 8px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:12px}.path-actions,.file-actions,.form-actions,.search-form{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.search-form>:first-child{flex:1 1 260px;min-width:0}
.node-list,.hit-list,.history ul{list-style:none;margin:0;padding:0;display:grid;gap:10px}.node-list li,.hit-list li,.history li{border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0;overflow-wrap:anywhere}
.node-list li{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.node-list :deep(.v-btn),.hit-list :deep(.v-btn){max-width:100%;height:auto;min-height:44px;text-align:left;white-space:normal;overflow-wrap:anywhere}
.editor{display:grid;gap:12px}.content-editor :deep(textarea){font-family:ui-monospace,SFMono-Regular,Consolas,monospace;line-height:1.55}.original-text,.host-memory pre{white-space:pre-wrap;overflow-wrap:anywhere}.host-memory pre{font-size:13px}
.host-memory summary{cursor:pointer;min-height:44px}.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}.host-memory :deep(.v-btn){min-height:44px}.host-memory :deep(.v-alert),.host-memory .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}}
</style>
