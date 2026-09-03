<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const metrics = ref(null)
const routingForm = ref({
  normal_provider_id: '',
  normal_model: '',
  deliberate_provider_id: '',
  deliberate_model: '',
})
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
  } catch (e) {
    error.value = e.message
  }
}

async function addProvider() {
  error.value = ''
  message.value = ''
  try {
    const body = { ...newProvider.value }
    if (!body.api_key) delete body.api_key
    const res = await api('/api/models/providers', { method: 'POST', body: JSON.stringify(body) })
    message.value = res.message || '模型供应商已成功保存'
    newProvider.value = { id: '', base_url: '', api_key: '', enabled: true, timeout_seconds: 60 }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function saveRouting() {
  error.value = ''
  message.value = ''
  try {
    const res = await api('/api/models/routing', { method: 'POST', body: JSON.stringify(routingForm.value) })
    message.value = res.message || '双层认知路由已热生效'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function testRoute(tier) {
  error.value = ''
  testResult.value = null
  try {
    const form = tier === 'normal'
      ? { provider_id: routingForm.value.normal_provider_id, model: routingForm.value.normal_model }
      : { provider_id: routingForm.value.deliberate_provider_id, model: routingForm.value.deliberate_model }
    testResult.value = await api('/api/models/test', { method: 'POST', body: JSON.stringify(form) })
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="models-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>模型集成与分级路由 (Models & Routing)</h1>
        <p class="muted">多供应商配置、零停机热切换与双层认知阶梯（Normal 快速直出 / Deliberate 深度推理）</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新状态</span>
      </button>
    </div>

    <p v-if="message" class="tag ok" style="margin-bottom: 16px;">✓ {{ message }}</p>
    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <!-- Configured Providers Bento Grid -->
    <div class="panel-header">
      <h2>已注册供应商 (Providers)</h2>
      <span class="tag">共 {{ data?.providers?.length || 0 }} 个供应商</span>
    </div>
    <div class="grid cards" style="margin-bottom: 24px;">
      <div v-for="p in data?.providers || []" :key="p.id" class="card bento-provider-card">
        <div class="provider-header">
          <h3><code>{{ p.id }}</code></h3>
          <span :class="p.enabled ? 'tag ok' : 'tag bad'">
            {{ p.enabled ? '已启用' : '已停用' }}
          </span>
        </div>
        <div class="kv">
          <span class="k">接口基址</span>
          <span class="v code-text">{{ p.base_url }}</span>
        </div>
        <div class="kv">
          <span class="k">密钥凭据</span>
          <span class="v code-text">{{ p.api_key_masked || '未配置' }}</span>
        </div>
        <div class="kv">
          <span class="k">超时设定</span>
          <span class="v">{{ p.timeout_seconds }} 秒</span>
        </div>
      </div>
    </div>

    <!-- Add / Update Provider Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>注册 / 更新供应商配置</h2>
        <span class="muted">支持 OpenAI 兼容格式接口</span>
      </div>
      <div class="toolbar form-row">
        <input v-model="newProvider.id" placeholder="供应商标识 (如 deepseek)" style="min-width: 160px" />
        <input v-model="newProvider.base_url" placeholder="API 基址 (如 https://api.deepseek.com/v1)" style="flex: 1; min-width: 260px" />
        <input v-model="newProvider.api_key" placeholder="API Key 凭据 (留空保留原值)" type="password" style="min-width: 220px" />
        <input v-model.number="newProvider.timeout_seconds" placeholder="超时(秒)" type="number" style="width: 80px" />
        <label class="checkbox-label">
          <input type="checkbox" v-model="newProvider.enabled" /> 启用
        </label>
        <button class="primary" @click="addProvider">保存供应商</button>
      </div>
    </div>

    <!-- Routing Hot-Reload Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>双层认知模型路由配置 (Hot Reload)</h2>
        <span class="muted">保存后即时无缝生效，无需重启服务</span>
      </div>
      <div class="routing-section">
        <div class="toolbar form-row">
          <span class="tier-label">常规模态 (Normal)</span>
          <input v-model="routingForm.normal_provider_id" placeholder="选用供应商 ID" style="min-width: 160px" />
          <input v-model="routingForm.normal_model" placeholder="调用模型名称 (如 deepseek-chat)" style="flex: 1; min-width: 240px" />
          <button class="small-btn" @click="testRoute('normal')">⚡ 连通性测试</button>
        </div>
        <div class="toolbar form-row">
          <span class="tier-label">深思模态 (Deliberate)</span>
          <input v-model="routingForm.deliberate_provider_id" placeholder="选用供应商 ID" style="min-width: 160px" />
          <input v-model="routingForm.deliberate_model" placeholder="调用模型名称 (如 deepseek-reasoner)" style="flex: 1; min-width: 240px" />
          <button class="small-btn" @click="testRoute('deliberate')">⚡ 连通性测试</button>
        </div>
        <div class="toolbar" style="margin-top: 14px;">
          <button class="primary" @click="saveRouting">应用并保存路由策略</button>
        </div>
        <div v-if="testResult" class="test-result-box" style="margin-top: 12px;">
          <span :class="testResult.success ? 'tag ok' : 'tag bad'">
            {{ testResult.success ? `✓ 测试成功 (${testResult.latency_ms}ms) 回复: ${testResult.response}` : `✕ 测试失败: ${testResult.error}` }}
          </span>
        </div>
      </div>
    </div>

    <!-- Routing Metrics Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>认知调用与性能看板 (Routing Metrics)</h2>
        <span class="muted">统计真实 Token 消耗、调用成功率与响应时延</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>认知层级</th>
            <th>供应商</th>
            <th>模型名称</th>
            <th>调用次数</th>
            <th>异常错误</th>
            <th>输入 Token</th>
            <th>输出 Token</th>
            <th>中位数时延 (p50)</th>
            <th>高分位时延 (p95)</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in metrics?.routes || []" :key="r.tier + r.provider_id + r.model">
            <td>
              <span class="tag" :class="r.tier === 'deliberate' ? 'warn' : 'ok'">{{ r.tier }}</span>
            </td>
            <td><code>{{ r.provider_id }}</code></td>
            <td class="highlight">{{ r.model }}</td>
            <td>{{ r.calls }}</td>
            <td>
              <span :class="r.errors > 0 ? 'bad-text' : ''">{{ r.errors }}</span>
            </td>
            <td>{{ r.prompt_tokens }}</td>
            <td>{{ r.completion_tokens }}</td>
            <td>{{ r.latency_p50_s != null ? `${r.latency_p50_s}s` : '—' }}</td>
            <td>{{ r.latency_p95_s != null ? `${r.latency_p95_s}s` : '—' }}</td>
          </tr>
          <tr v-if="!metrics?.routes?.length">
            <td colspan="9" class="muted" style="text-align: center; padding: 24px;">暂无认知模型调用统计</td>
          </tr>
        </tbody>
      </table>
      <div class="metrics-footer" style="margin-top: 12px; font-size: 0.86rem; color: var(--muted);">
        <span>自动升级深思次数 (Escalations): <strong style="color: #fff">{{ metrics?.escalation_total || 0 }}</strong></span>
        <span v-if="metrics?.recent_escalation_reasons?.length" style="margin-left: 12px;">
          · 最近升级原因: <code>{{ metrics.recent_escalation_reasons.join(' / ') }}</code>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-title h1 {
  margin: 0;
  font-size: 1.4rem;
}
.page-title p {
  margin: 4px 0 0;
  font-size: 0.85rem;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.panel-header h2 {
  margin: 0;
}

.bento-provider-card {
  padding: 16px 18px;
}
.provider-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.provider-header h3 {
  margin: 0;
}

.code-text {
  font-family: monospace;
  font-size: 0.84rem;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.88rem;
  color: var(--text);
  cursor: pointer;
}

.tier-label {
  min-width: 140px;
  font-weight: 600;
  color: #cbd5e1;
}

.small-btn {
  padding: 4px 10px;
  font-size: 0.82rem;
}

.highlight {
  color: #60a5fa;
  font-weight: 500;
}

.bad-text {
  color: #f87171;
  font-weight: 600;
}

.test-result-box {
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.25);
  border-radius: var(--radius-sm);
}
</style>
