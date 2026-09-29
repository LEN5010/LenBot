<script setup>
import { developerDetails } from '../composables/useDeveloperMode.js'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import HostSceneControl from '../components/HostSceneControl.vue'
import HostAudioPanel from '../components/HostAudioPanel.vue'

const route = useRoute(), router = useRouter()
const host = ref(null), scene = ref(''), activeOnly = ref(true)
const snapshot = ref(null), entries = ref([]), nextBefore = ref(null)
const loading = ref(false), hostLoading = ref(false), readError = ref('')
let selectionEpoch = 0
const selection = () => `${scene.value}\u0000${activeOnly.value}\u0000${selectionEpoch}`
const beginRead = useRequestGuard(selection), beginHost = useRequestGuard()
const options = computed(() => host.value?.scenes.map(item => ({
  title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene,
})) || [])
function timestamp(value) {
  if (value === null) return '未知时间'
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone: snapshot.value.timezone, hour12: false })
}
function roleLabel(role) {
  return { user: '输入上下文', assistant: '大脑回复', tool: '工具结果', system: '系统设定' }[role] || role
}
function content(value) {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
function raw(entry) { return JSON.stringify(entry.message, null, 2) }
function resetSelection() {
  ++selectionEpoch
  actionNotice.value = ''
  snapshot.value = null
  entries.value = []
  nextBefore.value = null
  readError.value = ''
  loading.value = false
}
function selectScene(value, fromRoute = false) {
  if (value === scene.value) return
  scene.value = value
  resetSelection()
  if (!fromRoute) router.replace({ name: 'host-history', query: { scene: value } })
  loadFirst()
}
function selectScope(value) {
  if (value === activeOnly.value) return
  activeOnly.value = value
  resetSelection()
  loadFirst()
}
async function loadFirst() {
  if (!scene.value) return
  const target = scene.value, scope = activeOnly.value
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(target)}/history?limit=50&active_only=${scope}`)
    if (!fresh()) return
    snapshot.value = value
    entries.value = value.entries
    nextBefore.value = value.next_before
    readError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function loadOlder() {
  if (!snapshot.value || nextBefore.value === null || loading.value) return
  const target = scene.value, scope = activeOnly.value, before = nextBefore.value
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api(`/api/host/scenes/${encodeURIComponent(target)}/history?before=${before}&limit=50&active_only=${scope}`)
    if (!fresh()) return
    if (value.compact_through !== snapshot.value.compact_through) {
      readError.value = '读取较早记录期间，当前会话压缩位置已变化。下方保留先前读取结果；请重读最新页。'
      return
    }
    entries.value = [...value.entries, ...entries.value]
    nextBefore.value = value.next_before
    readError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
async function readHost() {
  const fresh = beginHost()
  hostLoading.value = true
  try {
    const value = await api('/api/host/state')
    if (!fresh()) return
    host.value = value
    scene.value = typeof route.query.scene === 'string' ? route.query.scene : value.scenes[0].scene
    if (scene.value !== route.query.scene) router.replace({ name: 'host-history', query: { scene: scene.value } })
    await loadFirst()
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) hostLoading.value = false }
}
const acting=ref(false), actionNotice=ref('')
async function operate(action){
  if(acting.value||loading.value||!scene.value)return
  const text=action==='compact'?'调用当前大脑模型压缩完整旧对话（产生费用），不发送QQ？':'开启新上下文？原消息、记忆、安排和任务均保留，未读输入继续处理。'
  if(!window.confirm(text))return
  const target=scene.value;acting.value=true;actionNotice.value=''
  try{const result=await api(`/api/host/scenes/${encodeURIComponent(target)}/history/${action}?confirmed=true`,{method:'POST'});if(scene.value===target){actionNotice.value=result.message||'已完成一次手动压缩';await loadFirst()}}
  catch(e){if(scene.value===target)readError.value=`操作失败或结果未确认：${e.message}；请重读核对，不自动重试。`}
  finally{acting.value=false}
}
onMounted(readHost)
watch(() => route.query.scene, value => {
  if (typeof value === 'string' && host.value && value !== scene.value) selectScene(value, true)
})
</script>

<template>
  <div class="page-stack host-history">
    <div class="surface"><v-btn :disabled="acting||loading||!scene" @click="operate('compact')">手动压缩（调用模型）</v-btn> <v-btn :disabled="acting||loading||!scene" @click="operate('new-context')">开启新上下文</v-btn><p v-if="actionNotice">{{actionNotice}}</p></div>
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>大脑会话</h1>
      <p class="muted">读取本场景已保存的原生大脑上下文与回想，不触发模型。这是当前会话记录，不是长期记忆；手动压缩调用当前模型；新上下文保留原记录，不重放旧输入。</p></div>
      <v-btn variant="outlined" :loading="loading || hostLoading" :disabled="!scene" @click="loadFirst">重读最新页</v-btn></header>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次结果':'读取大脑会话失败'">{{ readError }}</v-alert>
    <section class="surface"><h2>读取范围</h2>
      <v-select :model-value="scene" :items="options" label="场景" hide-details="auto" :disabled="!host||acting" @update:model-value="selectScene" />
      <v-switch :model-value="activeOnly" label="仅本轮会话上下文" hide-details :disabled="!scene" @update:model-value="selectScope" />
      <p class="muted">{{ activeOnly?'仅显示压缩位置之后的大脑条目；上方回想仍单列。':'包含压缩或开启新上下文前的旧条目；旧记录不会因此重新进入当前大脑上下文。' }}</p>
      <p v-if="(loading || hostLoading) && !snapshot" role="status" class="muted">正在读取实际会话条目…</p>
    </section>
    <HostSceneControl v-if="scene" :key="`control-${scene}`" :scene="scene" />
    <HostAudioPanel v-if="scene" :key="scene" :scene="scene" />
    <template v-if="snapshot">
      <section class="surface"><div class="section-heading"><h2>当前回想</h2><span class="muted">按本次读取的压缩位置展示</span></div>
        <p v-if="snapshot.recap!==null" class="original-text">{{ snapshot.recap }}</p>
        <p v-else class="muted">尚无已保存回想。</p>
        <p class="muted">当前读取到 {{ entries.length }} 条原生上下文记录；后续有新消息或压缩时，需手动重读。</p>
      </section>
      <section class="surface"><div class="section-heading"><h2>原生条目</h2><span class="muted">页面顺序：较早 → 较新</span></div>
        <p v-if="!entries.length" class="muted">此范围暂无已保存条目。</p>
        <ol v-else class="entry-list"><li v-for="entry in entries" :key="entry.seq" class="entry-card">
          <div class="entry-heading"><strong>{{ roleLabel(entry.message.role) }}</strong><span class="muted">{{ timestamp(entry.created) }} · {{ entry.active?'当前上下文':'非当前上下文' }}</span></div>
          <p v-if="entry.message.content!==undefined && entry.message.content!==null" class="original-text">{{ content(entry.message.content) }}</p>
          <p v-else class="muted">无正文。</p>
          <template v-if="entry.message.tool_calls?.length"><div v-for="call in entry.message.tool_calls" :key="call.id" class="tool-call">
            <strong>调用工具：{{ call.function.name }}</strong><pre>{{ content(call.function.arguments) }}</pre></div></template>
          <details v-if="developerDetails"><summary>查看此条原生结构</summary><pre>{{ raw(entry) }}</pre></details>
        </li></ol>
        <div class="page-actions"><v-btn v-if="nextBefore!==null" variant="outlined" :loading="loading" @click="loadOlder">读取更早的 50 条</v-btn>
          <span v-else class="muted">此范围没有更早的条目。</span></div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.host-history{max-width:1100px;margin-inline:auto}
.page-intro,.section-heading,.entry-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 450px}.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface,.entry-card{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}
.original-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55}
.entry-list{list-style:none;margin:0;padding:0;display:grid;gap:12px}.entry-card{border:1px solid var(--line);border-radius:10px;padding:14px}.entry-card p{margin:10px 0}
.entry-heading strong{font-size:15px}.tool-call{border-left:3px solid var(--primary);padding:8px 12px;margin:12px 0;background:var(--selected-bg)}
.host-history pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;font-size:13px}.host-history details{border-top:1px solid var(--line);padding-top:8px}.host-history summary{cursor:pointer;min-height:44px}
.page-actions{display:flex;align-items:center;gap:14px;margin-top:16px}.host-history :deep(.v-btn){min-height:44px}.host-history :deep(.v-alert),.host-history .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}.entry-card{padding:12px}}
</style>
