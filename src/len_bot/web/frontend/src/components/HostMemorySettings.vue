<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'

const emit = defineEmits(['dirty'])
const snapshot = ref(null), draft = ref(null), identities = ref([])
const loading = ref(false), saving = ref(false)
const readError = ref(''), saveError = ref(''), savedNotice = ref('')
const beginRead = useRequestGuard(), beginSave = useRequestGuard()
const providers = computed(() => Object.keys(snapshot.value?.saved.models.providers || {}))
const configuredScenes = computed(() => Object.keys(snapshot.value?.saved.scenes || {}))
function copy(value) { return JSON.parse(JSON.stringify(value)) }
function numeric(value) { return value === '' ? '' : Number(value) }
function identityRows(value, scenes) {
  return scenes.map(scene => ({ scene, user_id: value?.[scene]?.user_id ?? '',
    api_key: '', api_key_configured: value?.[scene]?.api_key_configured ?? false }))
}
function normalizedMemory(value) {
  if (value === null) return null
  const common = { backend: value.backend, auto_recall: value.auto_recall,
    recall_budget_chars: value.recall_budget_chars, recall_limit: value.recall_limit,
    ingest: copy(value.ingest) }
  if (value.backend === 'local') return { ...common, local: copy(value.local) }
  return { ...common, openviking: {
    base_url: value.openviking.base_url, account_id: value.openviking.account_id,
    timeout_seconds: value.openviking.timeout_seconds, public_root: value.openviking.public_root,
    scenes: Object.fromEntries(Object.entries(value.openviking.scenes).sort(([left], [right]) => left.localeCompare(right)).map(([scene, item]) =>
      [scene, { user_id: item.user_id, api_key: null }])),
  } }
}
function body() {
  if (draft.value === null) return { memory: null }
  const common = { backend: draft.value.backend, auto_recall: draft.value.auto_recall,
    recall_budget_chars: draft.value.recall_budget_chars, recall_limit: draft.value.recall_limit,
    ingest: copy(draft.value.ingest) }
  if (draft.value.backend === 'local') return { memory: { ...common, local: copy(draft.value.local) } }
  return { memory: { ...common, openviking: {
    base_url: draft.value.openviking.base_url, account_id: draft.value.openviking.account_id,
    timeout_seconds: draft.value.openviking.timeout_seconds,
    public_root: draft.value.openviking.public_root,
    scenes: Object.fromEntries([...identities.value].sort((left, right) => left.scene.localeCompare(right.scene)).map(item => [item.scene, {
      user_id: item.user_id, api_key: item.api_key === '' ? null : item.api_key,
    }])),
  } } }
}
const dirty = computed(() => snapshot.value !== null && (
  JSON.stringify(body().memory) !== JSON.stringify(normalizedMemory(snapshot.value.saved.memory))
  || identities.value.some(item => item.api_key !== '')
))
watch(dirty, value => emit('dirty', value), { immediate: true })
onBeforeUnmount(() => emit('dirty', false))
function adopt(value) {
  snapshot.value = value
  draft.value = copy(value.saved.memory)
  identities.value = draft.value?.backend === 'openviking'
    ? identityRows(value.saved.memory.openviking.scenes, Object.keys(value.saved.scenes)) : []
  savedNotice.value = ''
}
async function read(confirmDiscard = true) {
  if (confirmDiscard && dirty.value && !window.confirm('放弃未保存的记忆配置草稿，重读根配置？')) return
  const fresh = beginRead()
  loading.value = true
  try {
    const value = await api('/api/host/settings')
    if (!fresh()) return
    adopt(value); readError.value = ''; saveError.value = ''
  } catch (error) { if (fresh()) readError.value = error.message }
  finally { if (fresh()) loading.value = false }
}
function chooseBackend(value) {
  if ((draft.value?.backend ?? 'none') === value) return
  if (dirty.value && !window.confirm('放弃当前记忆配置草稿并切换后端？')) return
  if (value === 'none') { draft.value = null; identities.value = [] }
  else if (value === 'local') {
    draft.value = { backend: 'local', auto_recall: true, recall_budget_chars: 1500, recall_limit: 5, ingest: null,
      local: { directory: '', embedding: null } }
    identities.value = []
  } else {
    draft.value = { backend: 'openviking', auto_recall: true, recall_budget_chars: 1500, recall_limit: 5, ingest: null,
      openviking: { base_url: '', account_id: '', timeout_seconds: 20, public_root: null } }
    identities.value = identityRows(null, configuredScenes.value)
  }
  saveError.value = ''; savedNotice.value = ''
}
function toggleEmbedding(value) {
  draft.value.local.embedding = value ? { provider: '', model: '', dimensions: null } : null
}
function toggleIngest(value) {
  draft.value.ingest = value ? {
    idle_seconds: 1800, min_messages: 50, max_age_seconds: 86400,
    batch_size: 100, max_steps: 8, timeout_seconds: 180,
  } : null
}
async function save() {
  if (!dirty.value || loading.value || saving.value) return
  const fresh = beginSave(), payload = body()
  saving.value = true; saveError.value = ''; savedNotice.value = ''
  try {
    const value = await api('/api/host/settings/memory', { method: 'PUT', body: JSON.stringify(payload) })
    if (!fresh()) return
    adopt(value)
    savedNotice.value = value.restart_required.memory
      ? '记忆配置已保存到根文件；当前运行后端、索引与回想方式不变，须按停机流程重启后生效。'
      : '记忆配置已保存到根文件；与当前运行值一致。'
  } catch (error) {
    if (fresh()) saveError.value = error.status >= 400 && error.status < 500
      ? `保存未被接受：${error.message}`
      : `保存结果未确认：${error.message} 草稿已保留；请重读根配置核对，不会自动重试。`
  } finally { if (fresh()) saving.value = false }
}
onMounted(() => read(false))
</script>

<template>
  <section class="surface memory-settings" aria-labelledby="memory-settings-title">
    <header class="section-heading"><div><p class="eyebrow">根配置 · 重启后生效</p><h2 id="memory-settings-title">记忆后端配置</h2></div>
      <v-btn variant="outlined" :loading="loading" :disabled="saving" @click="read()">重读保存值</v-btn></header>
    <p class="muted">只管理当前宿主根配置；保存不启用、不迁移或热切换后端。上方运行中的记忆树与检索不会因这里保存立即改变。</p>
    <v-alert v-if="readError" type="error" variant="tonal" role="alert" :title="snapshot?'读取失败 · 保留上次草稿':'读取记忆配置失败'">{{ readError }}</v-alert>
    <v-alert v-if="saveError" type="error" variant="tonal" role="alert">{{ saveError }}</v-alert>
    <v-alert v-if="savedNotice" type="success" variant="tonal" role="status">{{ savedNotice }}</v-alert>
    <p v-if="loading && !snapshot" role="status" class="muted">正在读取运行与保存的记忆后端设置…</p>
    <template v-if="snapshot">
      <div class="status-row"><strong>当前运行：{{ snapshot.running.memory?.backend ?? '未配置' }}</strong>
        <v-chip variant="tonal" :color="snapshot.restart_required.memory?'warning':'info'">{{ snapshot.restart_required.memory?'保存值待重启':'保存值与运行值一致' }}</v-chip></div>
      <details v-if="snapshot.running.memory"><summary>查看当前运行后端的实际设置</summary>
        <p>自动回想：{{ snapshot.running.memory.auto_recall?'启用':'关闭' }} · 字符预算 {{ snapshot.running.memory.recall_budget_chars }} · 返回上限 {{ snapshot.running.memory.recall_limit }}；自动抽取：{{ snapshot.running.memory.ingest===null?'关闭':'已配置' }}</p>
        <template v-if="snapshot.running.memory.backend==='local'"><p>本地目录：{{ snapshot.running.memory.local.directory }}</p>
          <p>检索：{{ snapshot.running.memory.local.embedding===null?'明确使用 FTS 文本检索（未配置向量）':`${snapshot.running.memory.local.embedding.provider} / ${snapshot.running.memory.local.embedding.model} · 维数 ${snapshot.running.memory.local.embedding.dimensions ?? '未指定'}` }}</p></template>
        <template v-else><p>服务：{{ snapshot.running.memory.openviking.base_url }} · 账户 {{ snapshot.running.memory.openviking.account_id }} · 公共根 {{ snapshot.running.memory.openviking.public_root ?? '未配置' }}</p>
          <ul><li v-for="(item,name) in snapshot.running.memory.openviking.scenes" :key="name">{{ sceneName(name) }} · 用户 {{ item.user_id }} · 密钥{{ item.api_key_configured?'已配置':'未配置' }}</li></ul></template>
      </details>
      <form @submit.prevent="save"><fieldset :disabled="loading || saving"><v-select :model-value="draft?.backend ?? 'none'" label="保存的记忆后端" :items="[{title:'不启用',value:'none'},{title:'本地文件',value:'local'},{title:'OpenViking',value:'openviking'}]" hide-details="auto" @update:model-value="chooseBackend" />
        <template v-if="draft"><div class="form-grid"><v-switch v-model="draft.auto_recall" label="自动回想" hide-details />
          <v-text-field :model-value="draft.recall_budget_chars" type="number" step="1" label="回想字符预算（100–12000）" hide-details="auto" @update:model-value="value=>draft.recall_budget_chars=numeric(value)" />
          <v-text-field :model-value="draft.recall_limit" type="number" step="1" label="回想命中上限（1–20）" hide-details="auto" @update:model-value="value=>draft.recall_limit=numeric(value)" /></div>
          <div class="ingest-editor"><h3>后台自动抽取 · 重启后生效</h3>
            <v-switch :model-value="draft.ingest!==null" label="启用后台小批抽取" hide-details @update:model-value="toggleIngest" />
            <p class="muted">首次启用只从当时已保存消息末尾之后读取新输入，不自动回填旧历史。保存不会立即运行，也不会据此声称已整理了记忆。</p>
            <p v-if="draft.ingest" class="muted">来源补抽排除尚未接入；启用期间完整 forget 不可用，普通删除不能解释为来源及备份都已遗忘。</p>
            <p v-if="draft.backend==='local'" class="muted">本地抽取使用专门的记忆模型，产生真实模型请求并可能计费；须在 <RouterLink :to="{name:'host-models'}">模型配置</RouterLink> 显式绑定 memory 用途，不能自动取大脑模型。</p>
            <v-alert v-if="draft.backend==='local' && draft.ingest!==null && snapshot.saved.models.roles.memory===null" type="warning" variant="tonal">最近读取的根配置尚无 memory 用途绑定；先保存明确模型，再重读本配置。否则后端会拒绝启用抽取。</v-alert>
            <p v-else class="muted">OpenViking 抽取由服务自身模型处理；此处不选择本地模型，也不把已提交任务说成完成。</p>
            <div v-if="draft.ingest" class="form-grid">
              <v-text-field :model-value="draft.ingest.idle_seconds" type="number" label="输入静置秒数" hide-details="auto" @update:model-value="value=>draft.ingest.idle_seconds=numeric(value)" />
              <v-text-field :model-value="draft.ingest.min_messages" type="number" step="1" label="最少消息数（1–100）" hide-details="auto" @update:model-value="value=>draft.ingest.min_messages=numeric(value)" />
              <v-text-field :model-value="draft.ingest.max_age_seconds" type="number" label="最长等待秒数" hide-details="auto" @update:model-value="value=>draft.ingest.max_age_seconds=numeric(value)" />
              <v-text-field :model-value="draft.ingest.batch_size" type="number" step="1" label="每批最多消息（1–100）" hide-details="auto" @update:model-value="value=>draft.ingest.batch_size=numeric(value)" />
              <v-text-field :model-value="draft.ingest.max_steps" type="number" step="1" label="每批最多模型步骤（1–30）" hide-details="auto" @update:model-value="value=>draft.ingest.max_steps=numeric(value)" />
              <v-text-field :model-value="draft.ingest.timeout_seconds" type="number" label="每批超时秒数" hide-details="auto" @update:model-value="value=>draft.ingest.timeout_seconds=numeric(value)" />
            </div>
          </div>
          <template v-if="draft.backend==='local'"><h3>本地文件后端</h3><p class="muted">目录必须在当前实例根目录内。可自行填写如 data/memory；这里不会替你选择路径或搬迁已有文件。</p>
            <v-text-field v-model="draft.local.directory" label="记忆目录（相对实例根或根内绝对路径）" hide-details="auto" />
            <v-switch :model-value="draft.local.embedding!==null" label="配置向量检索" hide-details @update:model-value="toggleEmbedding" />
            <p class="muted">{{ draft.local.embedding===null?'明确使用 FTS 文本检索；这不是向量失败后的自动降级。':'向量绑定失败会报错，不自动改用其他模型或文本后端。' }}</p>
            <div v-if="draft.local.embedding" class="form-grid"><v-select v-model="draft.local.embedding.provider" :items="providers" label="Embedding 提供方" hide-details="auto" />
              <v-text-field v-model="draft.local.embedding.model" label="Embedding 精确模型名" hide-details="auto" />
              <v-text-field :model-value="draft.local.embedding.dimensions ?? ''" type="number" step="1" label="维数（可不指定）" hide-details="auto" @update:model-value="value=>draft.local.embedding.dimensions=value===''?null:numeric(value)" /></div>
          </template>
          <template v-else><h3>OpenViking 后端</h3><p class="muted">地址、账户和每个场景身份都须显式填写；配置中的场景必须逐一覆盖且用户 ID 不重复。旧密钥仅在同地址、账户、用户身份不变时可用空输入保留。</p>
            <div class="form-grid"><v-text-field v-model="draft.openviking.base_url" label="服务 HTTP 地址" hide-details="auto" />
              <v-text-field v-model="draft.openviking.account_id" label="账户 ID" hide-details="auto" />
              <v-text-field :model-value="draft.openviking.timeout_seconds" type="number" label="请求超时（秒）" hide-details="auto" @update:model-value="value=>draft.openviking.timeout_seconds=numeric(value)" />
              <v-text-field :model-value="draft.openviking.public_root ?? ''" label="公共根 URI（可不设置）" hide-details="auto" @update:model-value="value=>draft.openviking.public_root=value===''?null:value" /></div>
            <div v-for="item in identities" :key="item.scene" class="identity"><h4>{{ sceneName(item.scene) }}</h4>
              <div class="form-grid"><v-text-field v-model="item.user_id" label="此场景 User ID" hide-details="auto" />
                <v-text-field v-model="item.api_key" type="password" autocomplete="new-password" label="替换 API 密钥（留空保留）" hide-details="auto" /></div>
              <p class="muted">已保存密钥：{{ item.api_key_configured?'是（原文不回显）':'否；须输入新密钥' }}。</p></div>
          </template>
        </template></fieldset>
        <p v-if="dirty" class="dirty-note" role="status">记忆配置草稿尚未保存。</p>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || loading">保存记忆配置</v-btn>
          <span class="muted">完整配置会由后端校验；当前运行后端保持原样。</span></div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.memory-settings{min-width:0;overflow-wrap:anywhere}.section-heading,.status-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}.section-heading h2{font-size:18px;margin:0 0 12px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}.memory-settings h3{font-size:16px;margin:20px 0 8px}.memory-settings h4{font-size:15px;margin:0 0 10px}
.memory-settings fieldset{border:0;padding:0;min-width:0;margin:18px 0}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px;margin:12px 0}.identity{border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0}
.ingest-editor{border-top:1px solid var(--line);margin-top:18px;padding-top:8px}
.memory-settings details{border-top:1px solid var(--line);padding:12px 0;margin-top:12px}.memory-settings summary{cursor:pointer;min-height:44px}.memory-settings details li{overflow-wrap:anywhere}
.dirty-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}.memory-settings :deep(.v-btn){min-height:44px}.memory-settings :deep(.v-alert),.memory-settings .muted{overflow-wrap:anywhere}
@media(max-width:600px){.section-heading>.v-btn{width:100%}.identity{padding:12px}}
</style>
