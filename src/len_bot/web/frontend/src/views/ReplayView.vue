<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const sceneId = ref('')
const since = ref('')
const until = ref('')
const overridesText = ref('[\n  {},\n  {"monitored_keywords": [], "speaking_budget_base_threshold": 1.0}\n]')
const runs = ref([])
const busy = ref(false)
const error = ref('')

async function run() {
  error.value = ''
  busy.value = true
  try {
    let overrides
    try {
      overrides = JSON.parse(overridesText.value)
    } catch {
      throw new Error('策略覆写参数必须为标准 JSON 数组格式')
    }
    const body = {
      scene_id: sceneId.value,
      since: since.value ? Number(since.value) : null,
      until: until.value ? Number(until.value) : null,
      overrides,
    }
    const res = await api('/api/replay', { method: 'POST', body: JSON.stringify(body) })
    runs.value = res.runs
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const DISPOSITION_CLASS = { wake: 'warn', observe: '', track: 'ok' }
</script>

<template>
  <div class="replay-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>策略离线实验室 (Replay Lab)</h1>
        <p class="muted">利用录制不可篡改的事件窗口进行离线推演，对比多组注意力阈值与发言预算策略参数</p>
      </div>
    </div>

    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <!-- Simulation Controls Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>回放环境与策略参数设置</h2>
      </div>
      <div class="toolbar">
        <input v-model="sceneId" placeholder="场景标识 (如 group:123)" style="min-width: 240px" />
        <input v-model="since" placeholder="起始时间戳 (Unix 秒, 可选)" />
        <input v-model="until" placeholder="截止时间戳 (Unix 秒, 可选)" />
        <button class="primary" :disabled="busy || !sceneId" @click="run">
          {{ busy ? '⚡ 正在离线推演…' : '▶ 执行策略对比仿真' }}
        </button>
      </div>
      <div style="margin-top: 12px;">
        <label class="muted" style="display: block; margin-bottom: 6px; font-size: 0.85rem;">
          策略覆写配置 JSON (数组中每一项代表一组独立运行参数，用于 A/B 策略对照)：
        </label>
        <textarea v-model="overridesText" rows="5" style="width: 100%; font-family: monospace; font-size: 0.85rem;"></textarea>
      </div>
    </div>

    <!-- Replay Simulation Results -->
    <div v-for="run in runs" :key="run.policy" class="panel result-panel" style="margin-top: 20px;">
      <div class="panel-header">
        <div>
          <h2>{{ run.policy }} 策略仿真报告</h2>
          <p class="muted">总回放消息: {{ run.summary.messages }} 条 · 唤醒决策: {{ run.summary.wake }} 次</p>
        </div>
        <span class="tag ok">推演完成</span>
      </div>

      <table>
        <thead>
          <tr>
            <th>消息时间</th>
            <th>发言成员</th>
            <th>原始输入内容</th>
            <th>注意力判定</th>
            <th>判定理由 (Reason)</th>
            <th>认知响应结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in run.rows" :key="i">
            <td>{{ new Date(r.timestamp * 1000).toLocaleTimeString() }}</td>
            <td><code>{{ r.actor_id }}</code></td>
            <td class="text-cell">{{ r.text }}</td>
            <td>
              <span class="tag" :class="DISPOSITION_CLASS[r.disposition] || ''">
                {{ r.disposition }}
              </span>
            </td>
            <td class="muted">{{ r.reason }}</td>
            <td class="highlight">{{ r.cognition || '—' }}</td>
          </tr>
          <tr v-if="!run.rows.length">
            <td colspan="6" class="muted" style="text-align: center; padding: 24px;">该策略下无命中消息</td>
          </tr>
        </tbody>
      </table>
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
.panel-header p {
  margin: 4px 0 0;
  font-size: 0.84rem;
}

.text-cell {
  color: #e2e8f0;
  font-weight: 500;
  max-width: 320px;
}

.highlight {
  color: #60a5fa;
  font-weight: 500;
}
</style>
