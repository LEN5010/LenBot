<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const metrics = ref(null)
const routingForm = ref({ normal_provider_id: '', normal_model: '', deliberate_provider_id: '', deliberate_model: '' })
const newProvider = ref({ id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 })
const testResult = ref(null)
const message = ref('')
const error = ref('')

onMounted(load)
async function load() {
  try {
    data.value = await api('/api/models/providers')
    metrics.value = await api('/api/models/metrics')
    if (data.value.routing) {
      routingForm.value = {
        normal_provider_id: data.value.routing.normal.provider_id,
        normal_model: data.value.routing.normal.model,
        deliberate_provider_id: data.value.routing.deliberate.provider_id,
        deliberate_model: data.value.routing.deliberate.model,
      }
    }
  } catch (e) { error.value = e.message }
}

async function addProvider() {
  error.value = ''; message.value = ''
  try {
    const body = { ...newProvider.value }
    if (!body.api_key) delete body.api_key
    const res = await api('/api/models/providers', { method: 'POST', body: JSON.stringify(body) })
    message.value = res.message
    newProvider.value = { id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 }
    await load()
  } catch (e) { error.value = e.message }
}

async function saveRouting() {
  error.value = ''; message.value = ''
  try {
    const res = await api('/api/models/routing', { method: 'POST', body: JSON.stringify(routingForm.value) })
    message.value = res.message
    await load()
  } catch (e) { error.value = e.message }
}

async function testRoute(tier) {
  error.value = ''; testResult.value = null
  try {
    const form = tier === 'normal'
      ? { provider_id: routingForm.value.normal_provider_id, model: routingForm.value.normal_model }
      : { provider_id: routingForm.value.deliberate_provider_id, model: routingForm.value.deliberate_model }
    testResult.value = await api('/api/models/test', { method: 'POST', body: JSON.stringify(form) })
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <h1>Models & Routing</h1>
    <p v-if="message" class="tag ok">{{ message }}</p>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <h2>Providers</h2>
    <div class="grid cards">
      <div v-for="p in data?.providers || []" :key="p.id" class="card">
        <h3>{{ p.id }} <span :class="p.enabled ? 'tag ok' : 'tag bad'">{{ p.enabled ? 'enabled' : 'disabled' }}</span></h3>
        <div class="kv"><span class="k">Base URL</span><span>{{ p.base_url }}</span></div>
        <div class="kv"><span class="k">API Key</span><span>{{ p.api_key_masked || '未设置' }}</span></div>
        <div class="kv"><span class="k">Timeout</span><span>{{ p.timeout_seconds }}s</span></div>
      </div>
    </div>

    <h2>新增 / 更新 Provider</h2>
    <div class="panel toolbar">
      <input v-model="newProvider.id" placeholder="provider id" />
      <input v-model="newProvider.base_url" placeholder="https://api.example.com/v1" style="min-width:260px" />
      <input v-model="newProvider.api_key" placeholder="API Key(留空保留旧值)" style="min-width:200px" />
      <label style="display:flex;align-items:center;gap:5px"><input type="checkbox" v-model="newProvider.enabled" /> 启用</label>
      <button class="primary" @click="addProvider">保存</button>
    </div>

    <h2>Routing(热更新)</h2>
    <div class="panel">
      <div class="toolbar">
        <span class="muted">Normal</span>
        <input v-model="routingForm.normal_provider_id" placeholder="provider id" />
        <input v-model="routingForm.normal_model" placeholder="model" />
        <button @click="testRoute('normal')">测试</button>
      </div>
      <div class="toolbar">
        <span class="muted">Deliberate</span>
        <input v-model="routingForm.deliberate_provider_id" placeholder="provider id" />
        <input v-model="routingForm.deliberate_model" placeholder="model" />
        <button @click="testRoute('deliberate')">测试</button>
      </div>
      <button class="primary" @click="saveRouting">保存路由</button>
      <div v-if="testResult" class="kv" style="margin-top:10px">
        <span class="k">测试结果</span>
        <span :class="testResult.success ? 'tag ok' : 'tag bad'">
          {{ testResult.success ? `OK ${testResult.latency_ms}ms: ${testResult.response}` : testResult.error }}
        </span>
      </div>
    </div>

    <h2>Routing Metrics</h2>
    <div class="panel">
      <table>
        <thead><tr><th>Tier</th><th>Provider</th><th>Model</th><th>Calls</th><th>Errors</th><th>Prompt tok</th><th>Completion tok</th><th>p50 (s)</th><th>p95 (s)</th></tr></thead>
        <tbody>
          <tr v-for="r in metrics?.routes || []" :key="r.tier + r.provider_id + r.model">
            <td>{{ r.tier }}</td><td>{{ r.provider_id }}</td><td>{{ r.model }}</td>
            <td>{{ r.calls }}</td><td>{{ r.errors }}</td>
            <td>{{ r.prompt_tokens }}</td><td>{{ r.completion_tokens }}</td>
            <td>{{ r.latency_p50_s ?? '—' }}</td><td>{{ r.latency_p95_s ?? '—' }}</td>
          </tr>
          <tr v-if="!metrics?.routes?.length"><td colspan="9" class="muted">暂无调用记录</td></tr>
        </tbody>
      </table>
      <p class="muted" style="margin-bottom:0">Escalations: {{ metrics?.escalation_total || 0 }}
        <span v-if="metrics?.recent_escalation_reasons?.length">· 最近: {{ metrics.recent_escalation_reasons.join(' / ') }}</span></p>
    </div>
  </div>
</template>
