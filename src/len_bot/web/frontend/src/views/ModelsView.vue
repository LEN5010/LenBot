<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref({ providers: [], routing: null })
const metrics = ref(null)
const catalogs = ref({})
const selectedModels = ref({})
const loadingModels = ref('')
const savingProvider = ref(false)
const message = ref('')
const error = ref('')
const testResult = ref(null)

const routingForm = ref({
  normal_provider_id: '', normal_model: '',
  deliberate_provider_id: '', deliberate_model: '',
  fallback_provider_id: '', fallback_model: '',
})
const providerForm = ref({ id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 })

onMounted(load)

async function load() {
  error.value = ''
  try {
    data.value = await api('/api/models/providers')
    metrics.value = await api('/api/models/metrics')
    for (const provider of data.value.providers || []) {
      if (!selectedModels.value[provider.id]) selectedModels.value[provider.id] = [...(provider.models || [])]
    }
    if (data.value.routing) {
      routingForm.value = {
        normal_provider_id: data.value.routing.normal.provider_id,
        normal_model: data.value.routing.normal.model,
        deliberate_provider_id: data.value.routing.deliberate.provider_id,
        deliberate_model: data.value.routing.deliberate.model,
        fallback_provider_id: data.value.routing.fallback?.provider_id || '',
        fallback_model: data.value.routing.fallback?.model || '',
      }
    }
  } catch (e) { error.value = e.message }
}

function providerById(id) { return (data.value.providers || []).find(provider => provider.id === id) }
function choicesFor(providerId) { return providerById(providerId)?.models || [] }
function routeChanged(tier) {
  const providerKey = `${tier}_provider_id`
  const modelKey = `${tier}_model`
  const choices = choicesFor(routingForm.value[providerKey])
  if (!choices.includes(routingForm.value[modelKey])) routingForm.value[modelKey] = choices[0] || ''
  if (tier === 'fallback' && !routingForm.value.fallback_provider_id) routingForm.value.fallback_model = ''
}

async function saveProvider() {
  error.value = ''; message.value = ''; savingProvider.value = true
  const providerId = providerForm.value.id.trim()
  try {
    const body = { ...providerForm.value }
    if (!body.api_key) delete body.api_key
    const result = await api('/api/models/providers', { method: 'POST', body: JSON.stringify(body) })
    message.value = result.message
    providerForm.value = { id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 }
    await load()
    await fetchModels(providerId)
  } catch (e) { error.value = e.message } finally { savingProvider.value = false }
}

async function fetchModels(providerId) {
  error.value = ''; loadingModels.value = providerId
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(providerId)}/models`)
    catalogs.value[providerId] = result.models
    const current = new Set(providerById(providerId)?.models || [])
    selectedModels.value[providerId] = result.models.filter(model => current.has(model))
    message.value = `已获取 ${result.models.length} 个模型，请勾选要使用的模型`
  } catch (e) { error.value = e.message } finally { loadingModels.value = '' }
}

async function saveModels(providerId) {
  error.value = ''; message.value = ''
  try {
    const result = await api(`/api/models/providers/${encodeURIComponent(providerId)}/models`, {
      method: 'POST', body: JSON.stringify({ models: selectedModels.value[providerId] || [] }),
    })
    message.value = result.message
    delete catalogs.value[providerId]
    await load()
  } catch (e) { error.value = e.message }
}

async function saveRouting() {
  error.value = ''; message.value = ''; testResult.value = null
  try {
    const result = await api('/api/models/routing', { method: 'POST', body: JSON.stringify(routingForm.value) })
    message.value = result.message
    await load()
  } catch (e) { error.value = e.message }
}

async function testRoute(tier) {
  error.value = ''; testResult.value = null
  const providerId = routingForm.value[`${tier}_provider_id`]
  const model = routingForm.value[`${tier}_model`]
  try {
    testResult.value = await api('/api/models/test', {
      method: 'POST', body: JSON.stringify({ provider_id: providerId, model }),
    })
  } catch (e) { error.value = e.message }
}

function tierName(tier) { return tier === 'deliberate' ? '思考模式' : '普通模式' }
</script>

<template>
  <div class="models-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>模型设置</h1>
        <p class="muted">添加接口、选择可用模型，然后指定聊天、思考和故障回退模型。</p>
      </div>
      <button @click="load">刷新</button>
    </div>

    <p v-if="message" class="notice success">{{ message }}</p>
    <p v-if="error" class="notice error">{{ error }}</p>

    <section class="bento-grid">
      <div class="bento-card step-card bento-col-5">
        <div class="step-number">1</div>
        <div class="bento-badge">添加供应商</div>
        <h2>连接模型接口</h2>
        <p class="muted">支持与 OpenAI 接口格式兼容的服务。</p>
        <form class="provider-form" @submit.prevent="saveProvider">
          <label>供应商名称<input v-model="providerForm.id" required placeholder="例如：深度求索" /></label>
          <label>接口地址<input v-model="providerForm.base_url" required placeholder="https://example.com/v1" /></label>
          <label class="wide">接口密钥<input v-model="providerForm.api_key" type="password" placeholder="更新时留空会保留原密钥" /></label>
          <label>超时时间<input v-model.number="providerForm.timeout_seconds" type="number" min="1" /><span class="input-unit">秒</span></label>
          <label class="switch-row"><input v-model="providerForm.enabled" type="checkbox" />立即启用</label>
          <button class="primary wide" :disabled="savingProvider">{{ savingProvider ? '正在连接…' : '保存并获取模型' }}</button>
        </form>
      </div>

      <div class="bento-card bento-col-7">
        <div class="step-number">2</div>
        <div class="bento-badge">选择可用模型</div>
        <h2>已添加的供应商</h2>
        <p class="muted">获取模型列表后，只勾选你准备给机器人使用的模型。</p>

        <div class="provider-list">
          <article v-for="provider in data.providers" :key="provider.id" class="provider-item">
            <div class="provider-title">
              <div><strong>{{ provider.id }}</strong><span>{{ provider.base_url }}</span></div>
              <span :class="provider.enabled ? 'tag ok' : 'tag bad'">{{ provider.enabled ? '已启用' : '已停用' }}</span>
            </div>
            <div class="provider-meta"><span>密钥：{{ provider.api_key_masked || '未设置' }}</span><span>超时：{{ provider.timeout_seconds }} 秒</span></div>

            <template v-if="catalogs[provider.id]">
              <div class="model-checks">
                <label v-for="model in catalogs[provider.id]" :key="model">
                  <input v-model="selectedModels[provider.id]" type="checkbox" :value="model" />{{ model }}
                </label>
              </div>
              <button class="primary small-btn" @click="saveModels(provider.id)">保存勾选结果</button>
            </template>
            <template v-else>
              <div class="model-chips">
                <span v-for="model in provider.models || []" :key="model" class="model-chip">{{ model }}</span>
                <span v-if="!provider.models?.length" class="muted">还没有选择模型</span>
              </div>
              <button class="small-btn" :disabled="loadingModels === provider.id" @click="fetchModels(provider.id)">
                {{ loadingModels === provider.id ? '正在获取…' : '获取模型列表' }}
              </button>
            </template>
          </article>
          <div v-if="!data.providers.length" class="empty">请先添加一个供应商</div>
        </div>
      </div>
    </section>

    <section class="panel route-panel">
      <div class="panel-header">
        <div><div class="bento-badge">3 · 指定机器人用哪个模型</div><h2>聊天模型安排</h2></div>
        <button class="primary" @click="saveRouting">保存并立即生效</button>
      </div>

      <div class="route-grid">
        <article class="route-card">
          <div class="route-heading"><span class="route-icon blue">聊</span><div><strong>普通模式</strong><p>负责日常群聊和一般判断</p></div></div>
          <label>供应商<select v-model="routingForm.normal_provider_id" @change="routeChanged('normal')"><option v-for="p in data.providers" :key="p.id" :value="p.id">{{ p.id }}</option></select></label>
          <label>模型<select v-model="routingForm.normal_model"><option v-for="model in choicesFor(routingForm.normal_provider_id)" :key="model" :value="model">{{ model }}</option></select></label>
          <button class="small-btn" :disabled="!routingForm.normal_model" @click="testRoute('normal')">测试连接</button>
        </article>

        <article class="route-card">
          <div class="route-heading"><span class="route-icon violet">思</span><div><strong>思考模式</strong><p>在查到较多资料或需要多步分析时接手</p></div></div>
          <label>供应商<select v-model="routingForm.deliberate_provider_id" @change="routeChanged('deliberate')"><option v-for="p in data.providers" :key="p.id" :value="p.id">{{ p.id }}</option></select></label>
          <label>模型<select v-model="routingForm.deliberate_model"><option v-for="model in choicesFor(routingForm.deliberate_provider_id)" :key="model" :value="model">{{ model }}</option></select></label>
          <button class="small-btn" :disabled="!routingForm.deliberate_model" @click="testRoute('deliberate')">测试连接</button>
        </article>

        <article class="route-card">
          <div class="route-heading"><span class="route-icon green">备</span><div><strong>回退模型</strong><p>主接口不可用时自动尝试</p></div></div>
          <label>供应商<select v-model="routingForm.fallback_provider_id" @change="routeChanged('fallback')"><option value="">不设置回退</option><option v-for="p in data.providers" :key="p.id" :value="p.id">{{ p.id }}</option></select></label>
          <label>模型<select v-model="routingForm.fallback_model" :disabled="!routingForm.fallback_provider_id"><option v-for="model in choicesFor(routingForm.fallback_provider_id)" :key="model" :value="model">{{ model }}</option></select></label>
          <button class="small-btn" :disabled="!routingForm.fallback_model" @click="testRoute('fallback')">测试连接</button>
        </article>
      </div>

      <p v-if="testResult" :class="testResult.success ? 'notice success' : 'notice error'">
        {{ testResult.success ? `连接成功，耗时 ${testResult.latency_ms} 毫秒` : `连接失败：${testResult.error}` }}
      </p>
    </section>

    <details class="panel metrics-panel">
      <summary>查看模型调用明细</summary>
      <p class="muted">仅在排查模型速度或消耗时需要查看。</p>
      <table>
        <thead><tr><th>用途</th><th>供应商</th><th>模型</th><th>调用</th><th>失败</th><th>输入量</th><th>输出量</th><th>常见耗时</th></tr></thead>
        <tbody>
          <tr v-for="row in metrics?.routes || []" :key="row.tier + row.provider_id + row.model">
            <td>{{ tierName(row.tier) }}</td><td>{{ row.provider_id }}</td><td>{{ row.model }}</td><td>{{ row.calls }}</td><td>{{ row.errors }}</td><td>{{ row.prompt_tokens }}</td><td>{{ row.completion_tokens }}</td><td>{{ row.latency_p50_s ?? '—' }} 秒</td>
          </tr>
          <tr v-if="!metrics?.routes?.length"><td colspan="8" class="muted">还没有调用记录</td></tr>
        </tbody>
      </table>
    </details>
  </div>
</template>

<style scoped>
.bento-col-5 { grid-column: span 5; }
.bento-col-7 { grid-column: span 7; }
.step-card { background: linear-gradient(145deg, rgba(255,255,255,.88), rgba(235,244,255,.76)); }
.step-number { position: absolute; right: 20px; top: 12px; color: rgba(37,99,235,.11); font-size: 4rem; font-weight: 850; line-height: 1; }
.provider-form { margin-top: 18px; display: grid; grid-template-columns: 1fr 1fr; gap: 13px; }
label { position: relative; display: flex; flex-direction: column; gap: 7px; color: var(--text-soft); font-size: .84rem; font-weight: 650; }
label input, label select { width: 100%; }
.wide { grid-column: 1 / -1; }
.input-unit { position: absolute; right: 12px; bottom: 12px; color: var(--muted); }
.switch-row { min-height: 42px; flex-direction: row; align-items: center; }
.switch-row input { width: 18px; min-height: 18px; }
.provider-list { display: grid; gap: 12px; margin-top: 17px; }
.provider-item { padding: 15px; background: rgba(255,255,255,.64); border: 1px solid var(--border); border-radius: 14px; }
.provider-title { display: flex; justify-content: space-between; gap: 12px; }
.provider-title strong { display: block; color: var(--text); }
.provider-title div span { display: block; max-width: 460px; margin-top: 3px; color: var(--muted); font-size: .75rem; overflow: hidden; text-overflow: ellipsis; }
.provider-meta { display: flex; gap: 16px; margin: 10px 0; color: var(--muted); font-size: .78rem; }
.model-chips { display: flex; flex-wrap: wrap; gap: 7px; margin: 10px 0 12px; }
.model-chip { padding: 5px 8px; color: #29578f; font-size: .75rem; background: #edf4fd; border-radius: 8px; }
.model-checks { max-height: 220px; margin: 12px 0; padding: 10px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; overflow-y: auto; background: rgba(239,246,255,.75); border-radius: 11px; }
.model-checks label { min-width: 0; padding: 6px; flex-direction: row; align-items: center; font-family: monospace; font-size: .75rem; overflow-wrap: anywhere; }
.model-checks input { width: 16px; min-height: 16px; }
.empty { padding: 32px; color: var(--muted); text-align: center; background: rgba(239,246,255,.55); border-radius: 13px; }
.route-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }
.route-card { padding: 17px; display: grid; gap: 12px; background: rgba(248,251,255,.72); border: 1px solid var(--border); border-radius: 16px; }
.route-heading { display: flex; align-items: center; gap: 11px; }
.route-heading strong { color: var(--text); }
.route-heading p { margin: 3px 0 0; color: var(--muted); font-size: .76rem; line-height: 1.4; }
.route-icon { width: 38px; height: 38px; display: grid; place-items: center; color: white; border-radius: 12px; font-weight: 750; }
.route-icon.blue { background: linear-gradient(135deg, #3b82f6, #2563eb); }
.route-icon.violet { background: linear-gradient(135deg, #8b5cf6, #6366f1); }
.route-icon.green { background: linear-gradient(135deg, #10b981, #059669); }
.notice { margin: 0 0 18px; padding: 11px 14px; border-radius: 12px; font-size: .88rem; }
.notice.success { color: var(--ok); background: var(--ok-bg); border: 1px solid rgba(5,150,105,.14); }
.notice.error { color: var(--bad); background: var(--bad-bg); border: 1px solid rgba(220,38,38,.14); }
.route-panel .notice { margin: 16px 0 0; }
.metrics-panel summary { color: var(--text); font-weight: 700; cursor: pointer; }
@media (max-width: 1120px) { .bento-col-5, .bento-col-7 { grid-column: span 12; } .route-grid { grid-template-columns: 1fr; } }
@media (max-width: 620px) { .provider-form, .model-checks { grid-template-columns: 1fr; } .wide { grid-column: auto; } }
</style>
