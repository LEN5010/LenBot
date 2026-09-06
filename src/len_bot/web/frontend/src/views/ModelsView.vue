<script setup>
import { computed, ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref({ providers: [], routing: null }), metrics = ref(null)
const catalogs = ref({}), selectedModels = ref({}), loadingModels = ref('')
const savingProvider = ref(false), savingRouting = ref(false), testingRole = ref('')
const editingProvider = ref(''), deletingProvider = ref('')
const message = ref(''), error = ref(''), testResult = ref(null)
const roles = [
  { key: 'conversation', name: '对话模型', icon: '聊', description: '理解群聊和图片，选择文字、表情与沉默' },
  { key: 'work', name: '工作模型', icon: '查', description: '在后台查询资料、解题和形成结果，供对话模型接话' },
]
const emptyProfile = () => ({ provider_id: '', model: '', reasoning_effort: '' })
const routingForm = ref({ conversation: emptyProfile(), work: emptyProfile() })
const emptyProvider = () => ({ id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 })
const providerForm = ref(emptyProvider())
const canSaveRouting = computed(() => roles.every(({ key }) => routingForm.value[key].provider_id && routingForm.value[key].model.trim()))

onMounted(load)
async function load() {
  error.value = ''
  try {
    const [providers, usage] = await Promise.all([api('/api/models/providers'), api('/api/models/metrics')])
    data.value = providers; metrics.value = usage
    for (const provider of providers.providers) {
      if (!catalogs.value[provider.id]) selectedModels.value[provider.id] = [...(provider.models || [])]
    }
    for (const { key } of roles) routingForm.value[key] = { ...emptyProfile(), ...(providers.routing?.[key] || {}) }
  } catch (e) { error.value = e.message }
}
function providerById(id) { return data.value.providers.find(provider => provider.id === id) }
function choicesFor(id) { return providerById(id)?.models || [] }
function inUse(id) { return roles.some(({ key }) => data.value.routing?.[key]?.provider_id === id) }
function routeChanged(role) { routingForm.value[role].model = ''; testResult.value = null }
function editProvider(provider) {
  editingProvider.value = provider.id
  providerForm.value = { id: provider.id, base_url: provider.base_url, api_key: '', enabled: provider.enabled, timeout_seconds: provider.timeout_seconds }
}
function clearProvider() { editingProvider.value = ''; providerForm.value = emptyProvider() }
async function saveProvider() {
  error.value = ''; message.value = ''; savingProvider.value = true
  try {
    const body = { ...providerForm.value }
    if (!body.api_key) delete body.api_key
    const result = await api('/api/models/providers', { method: 'POST', body: JSON.stringify(body) })
    message.value = result.message
    clearProvider(); await load()
  } catch (e) { error.value = e.message } finally { savingProvider.value = false }
}
async function deleteProvider(id) {
  error.value = ''; message.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(id)}`, { method: 'DELETE' })
    message.value = result.message; deletingProvider.value = ''
    delete catalogs.value[id]; delete selectedModels.value[id]
    if (editingProvider.value === id) clearProvider()
    await load()
  } catch (e) { error.value = e.message }
}
async function fetchModels(id) {
  error.value = ''; loadingModels.value = id
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(id)}/models`)
    catalogs.value[id] = result.models
    selectedModels.value[id] = [...(providerById(id)?.models || [])]
    message.value = `已取得 ${result.models.length} 个模型，可勾选常用项`
  } catch (e) { error.value = e.message } finally { loadingModels.value = '' }
}
async function saveModels(id) {
  error.value = ''; message.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(id)}/models`, { method: 'POST', body: JSON.stringify({ models: selectedModels.value[id] || [] }) })
    message.value = result.message; delete catalogs.value[id]; await load()
  } catch (e) { error.value = e.message }
}
function profile(role) {
  const item = routingForm.value[role]
  return { provider_id: item.provider_id, model: item.model.trim(), reasoning_effort: item.reasoning_effort?.trim() || null }
}
async function saveRouting() {
  error.value = ''; message.value = ''; savingRouting.value = true
  try {
    const result = await api('/api/models/routing', { method: 'POST', body: JSON.stringify({ conversation: profile('conversation'), work: profile('work') }) })
    message.value = result.message; await load()
  } catch (e) { error.value = e.message } finally { savingRouting.value = false }
}
async function testRoute(role) {
  error.value = ''; testResult.value = null; testingRole.value = role
  try { testResult.value = { ...(await api('/api/models/test', { method: 'POST', body: JSON.stringify(profile(role)) })), role } }
  catch (e) { error.value = e.message } finally { testingRole.value = '' }
}
function roleName(role) { return { conversation: '对话', work: '工作', reflection: '反思' }[role] || role }
</script>

<template>
  <div class="models-view">
    <div class="toolbar"><div class="page-title"><h1>模型设置</h1><p class="muted">对话与后台工作分别配置，两者都直接处理图片和工具。</p></div><button @click="load">刷新</button></div>
    <p v-if="message" class="notice success" role="status">{{ message }}</p>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <section class="bento-grid">
      <div class="bento-card step-card bento-col-5">
        <div class="step-number">1</div><div class="bento-badge">模型接口</div><h2>{{ editingProvider ? '编辑供应商' : '添加供应商' }}</h2>
        <p class="muted">使用 OpenAI 兼容接口地址，密钥只用于后台连接。</p>
        <form class="provider-form" @submit.prevent="saveProvider">
          <label>供应商名称<input v-model="providerForm.id" :disabled="!!editingProvider" required placeholder="接口名称" /></label>
          <label>接口地址<input v-model="providerForm.base_url" required placeholder="https://example.com/v1" /></label>
          <label class="wide">接口密钥<input v-model="providerForm.api_key" type="password" autocomplete="new-password" placeholder="编辑时留空保留原密钥" /></label>
          <label>超时时间（秒）<input v-model.number="providerForm.timeout_seconds" type="number" min="1" required /></label>
          <label class="switch-row"><input v-model="providerForm.enabled" type="checkbox" />启用供应商</label>
          <div class="wide button-row"><button class="primary" :disabled="savingProvider">{{ savingProvider ? '正在保存…' : '保存供应商' }}</button><button v-if="editingProvider" type="button" @click="clearProvider">取消编辑</button></div>
        </form>
      </div>
      <div class="bento-card bento-col-7">
        <div class="step-number">2</div><div class="bento-badge">可选模型</div><h2>已添加的供应商</h2><p class="muted">获取接口目录，选出常用模型。目录名称本身不代表已通过能力检查。</p>
        <div class="provider-list">
          <article v-for="provider in data.providers" :key="provider.id" class="provider-item">
            <div class="provider-title"><div><strong>{{ provider.id }}</strong><span>{{ provider.base_url }}</span></div><span :class="provider.enabled ? 'tag ok' : 'tag bad'">{{ provider.enabled ? '已启用' : '已停用' }}</span></div>
            <div class="provider-meta"><span>密钥：{{ provider.api_key_masked || '未设置' }}</span><span>超时 {{ provider.timeout_seconds }} 秒</span></div>
            <div class="button-row"><button class="small-btn" @click="editProvider(provider)">编辑接口</button><button class="small-btn" :disabled="inUse(provider.id)" @click="deletingProvider = provider.id">删除</button></div>
            <div v-if="deletingProvider === provider.id" class="notice error">删除接口会同时移除保存的密钥。<div class="button-row"><button @click="deleteProvider(provider.id)">删除这个接口</button><button @click="deletingProvider = ''">取消</button></div></div>
            <template v-if="catalogs[provider.id]">
              <div class="model-checks"><label v-for="model in catalogs[provider.id]" :key="model"><input v-model="selectedModels[provider.id]" type="checkbox" :value="model" />{{ model }}</label><span v-if="!catalogs[provider.id].length" class="muted">接口目录为空</span></div>
              <button class="primary small-btn" @click="saveModels(provider.id)">保存勾选结果</button>
            </template>
            <template v-else>
              <div class="model-chips"><span v-for="model in provider.models || []" :key="model" class="model-chip">{{ model }}</span><span v-if="!provider.models?.length" class="muted">还没有选择常用模型</span></div>
              <button class="small-btn" :disabled="loadingModels === provider.id || !provider.enabled" @click="fetchModels(provider.id)">{{ loadingModels === provider.id ? '正在获取…' : '获取模型列表' }}</button>
            </template>
          </article>
          <div v-if="!data.providers.length" class="empty">添加接口后即可配置两条模型路由</div>
        </div>
      </div>
    </section>
    <section class="panel route-panel">
      <div class="panel-header"><div><div class="bento-badge">3 · 指定模型</div><h2>对话与工作</h2></div><button class="primary" :disabled="!canSaveRouting || savingRouting" @click="saveRouting">{{ savingRouting ? '正在保存…' : '保存模型配置' }}</button></div>
      <p class="muted">两条路由均需支持原生图片、函数工具和指定工具终结。修改从下一次运行生效；后台工作会保留启动时的模型。</p>
      <div class="route-grid">
        <article v-for="role in roles" :key="role.key" class="route-card">
          <div class="route-heading"><span class="route-icon">{{ role.icon }}</span><div><strong>{{ role.name }}</strong><p>{{ role.description }}</p></div></div>
          <label>供应商<select v-model="routingForm[role.key].provider_id" @change="routeChanged(role.key)"><option value="">请选择供应商</option><option v-for="provider in data.providers" :key="provider.id" :value="provider.id" :disabled="!provider.enabled">{{ provider.id }}{{ provider.enabled ? '' : '（已停用）' }}</option></select></label>
          <label>模型<input v-model="routingForm[role.key].model" :list="`models-${role.key}`" placeholder="选择或输入接口中的完整模型名" /><datalist :id="`models-${role.key}`"><option v-for="model in choicesFor(routingForm[role.key].provider_id)" :key="model" :value="model" /></datalist></label>
          <label>推理强度<input v-model="routingForm[role.key].reasoning_effort" list="reasoning-levels" placeholder="留空使用模型默认值" /></label>
          <button class="small-btn" :disabled="!routingForm[role.key].provider_id || !routingForm[role.key].model.trim() || !!testingRole" @click="testRoute(role.key)">{{ testingRole === role.key ? '正在检查图文与工具…' : '检查图文与工具能力' }}</button>
        </article>
      </div>
      <datalist id="reasoning-levels"><option value="low" /><option value="medium" /><option value="high" /></datalist>
      <p class="muted">能力检查使用合成图片和本地工具回执，共调用两次模型，不产生群聊消息。</p>
      <div v-if="testResult" :class="testResult.success ? 'notice success' : 'notice error'" role="status"><strong>{{ roleName(testResult.role) }}模型{{ testResult.success ? '能力检查通过' : '能力检查未通过' }}</strong> · {{ testResult.latency_ms }} 毫秒<p v-if="testResult.error">{{ testResult.error }}</p><div class="check-results"><span>图片读取 {{ testResult.checks?.image_reading ? '✓' : '—' }}</span><span>指定工具 {{ testResult.checks?.forced_tool ? '✓' : '—' }}</span><span>工具续接 {{ testResult.checks?.tool_continuation ? '✓' : '—' }}</span></div></div>
    </section>
    <details class="panel metrics-panel"><summary>模型调用明细</summary><div class="table-scroll"><table><thead><tr><th>用途</th><th>供应商</th><th>模型</th><th>调用</th><th>失败</th><th>输入量</th><th>输出量</th><th>中位耗时</th></tr></thead><tbody><tr v-for="row in metrics?.routes || []" :key="row.tier + row.provider_id + row.model"><td>{{ roleName(row.tier) }}</td><td>{{ row.provider_id }}</td><td>{{ row.model }}</td><td>{{ row.calls }}</td><td>{{ row.errors }}</td><td>{{ row.prompt_tokens }}</td><td>{{ row.completion_tokens }}</td><td>{{ row.latency_p50_s ?? '—' }} 秒</td></tr><tr v-if="!metrics?.routes?.length"><td colspan="8" class="muted">还没有调用记录</td></tr></tbody></table></div></details>
  </div>
</template>

<style scoped>
.bento-col-5 { grid-column:span 5 }.bento-col-7 { grid-column:span 7 }.step-card { background:linear-gradient(145deg,rgba(255,255,255,.88),rgba(235,244,255,.76)) }.step-number { position:absolute;right:20px;top:12px;color:rgba(37,99,235,.11);font-size:4rem;font-weight:850;line-height:1 }.provider-form { margin-top:18px;display:grid;grid-template-columns:1fr 1fr;gap:13px }label { display:flex;flex-direction:column;gap:7px;color:var(--text-soft);font-size:.84rem;font-weight:650 }label input,label select { width:100% }.wide { grid-column:1/-1 }.switch-row { min-height:42px;flex-direction:row;align-items:center }.switch-row input { width:18px;min-height:18px }.provider-list { display:grid;gap:12px;margin-top:17px }.provider-item { padding:15px;background:rgba(255,255,255,.64);border:1px solid var(--border);border-radius:14px }.provider-title { display:flex;justify-content:space-between;gap:12px }.provider-title strong,.provider-title div span { display:block }.provider-title div span { margin-top:3px;color:var(--muted);font-size:.75rem;overflow-wrap:anywhere }.provider-meta,.button-row,.check-results { display:flex;gap:12px;flex-wrap:wrap;margin:10px 0 }.provider-meta { color:var(--muted);font-size:.78rem }.model-chips { display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 12px }.model-chip { padding:5px 8px;color:#29578f;font-size:.75rem;background:#edf4fd;border-radius:8px;overflow-wrap:anywhere }.model-checks { max-height:240px;margin:12px 0;padding:10px;display:grid;gap:7px;overflow-y:auto;background:rgba(239,246,255,.75);border-radius:11px }.model-checks label { flex-direction:row;align-items:center;overflow-wrap:anywhere;font-size:.78rem }.model-checks input { width:16px;min-height:16px }.small-btn { padding:7px 11px;font-size:.8rem }.route-panel { margin-top:20px }.route-grid { display:grid;grid-template-columns:1fr 1fr;gap:18px }.route-card { padding:20px;border:1px solid var(--border);border-radius:15px;background:rgba(255,255,255,.55);display:grid;gap:14px }.route-heading { display:flex;gap:12px;align-items:center }.route-heading strong { color:var(--text) }.route-heading p { margin:4px 0 0;color:var(--muted);font-size:.8rem }.route-icon { display:grid;place-items:center;width:42px;height:42px;flex-shrink:0;border-radius:12px;background:#e8f1ff;color:#2563eb;font-weight:750 }.notice { padding:12px 16px;border-radius:12px;margin:14px 0;overflow-wrap:anywhere }.notice.success { color:#166534;background:#ecfdf5 }.notice.error { color:#991b1b;background:#fef2f2 }.metrics-panel { margin-top:20px }summary { cursor:pointer;font-weight:700 }.table-scroll { overflow-x:auto;margin-top:14px }.empty { padding:24px;color:var(--muted);text-align:center }@media(max-width:980px){.bento-col-5,.bento-col-7{grid-column:span 12}}@media(max-width:620px){.provider-form,.route-grid{grid-template-columns:1fr}}
</style>
