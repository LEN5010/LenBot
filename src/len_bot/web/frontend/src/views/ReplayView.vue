<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const sceneId = ref('')
const since = ref('')
const until = ref('')
const runs = ref([])
const busy = ref(false)
const error = ref('')
const toolMode = ref('mock')
const deliveryMode = ref('shadow')

async function run() {
  error.value = ''
  busy.value = true
  try {
    const body = {
      scene_id: sceneId.value,
      since: since.value ? Number(since.value) : null,
      until: until.value ? Number(until.value) : null,
      tool_mode: toolMode.value,
      delivery_mode: deliveryMode.value,
    }
    const res = await api('/api/replay', { method: 'POST', body: JSON.stringify(body) })
    runs.value = res.runs
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const DECISION_CLASS = { speak: 'ok', silence: '' }
function decisionLabel(value) { return value === 'speak' ? '准备发言' : value === 'silence' ? '选择沉默' : value }
</script>

<template>
  <div class="replay-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>聊天回放</h1>
        <p class="muted">用已经发生的聊天重新测试机器人会怎样理解和回应。</p>
      </div>
    </div>

    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <!-- Simulation Controls Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>回放范围</h2>
      </div>
      <div class="toolbar">
        <input v-model="sceneId" placeholder="群聊标识，例如 group:123" style="min-width: 240px" />
        <input v-model="since" placeholder="开始时间，可不填" />
        <input v-model="until" placeholder="结束时间，可不填" />
        <select v-model="toolMode"><option value="mock">固定工具观测</option><option value="real">真实只读查询</option></select>
        <select v-model="deliveryMode"><option value="shadow">只看候选</option><option value="simulated">隔离模拟送达</option></select>
        <button class="primary" :disabled="busy || !sceneId" @click="run">
          {{ busy ? '正在回放…' : '开始回放' }}
        </button>
      </div>
    </div>

    <!-- Replay Simulation Results -->
    <div v-for="run in runs" :key="run.policy" class="panel result-panel" style="margin-top: 20px;">
      <div class="panel-header">
        <div>
          <h2>{{ run.policy }} 回放报告</h2>
          <p class="muted">认知 {{ run.summary.cognition }} 次 · 主动静默 {{ run.summary.silence }} 次 · 拟发言 {{ run.summary.would_speak }} 次</p>
        </div>
        <span class="tag" :class="run.execution?.completed ? 'ok' : 'bad'">{{ run.execution?.completed ? '链路完成，行为待评阅' : '回放失败或未完成' }}</span>
      </div>

      <table>
        <thead>
          <tr>
            <th>消息时间</th>
            <th>发言成员</th>
            <th>原始输入内容</th>
            <th>社交决策</th>
            <th>为什么这样决定</th>
            <th>认知响应结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in run.rows" :key="i">
            <td>{{ new Date(r.timestamp * 1000).toLocaleTimeString() }}</td>
            <td><code>{{ r.actor_id }}</code></td>
            <td class="text-cell">{{ r.text }}</td>
            <td>
              <span class="tag" :class="DECISION_CLASS[r.decision] || ''">
                {{ decisionLabel(r.decision) }}
              </span>
            </td>
            <td class="muted">{{ r.reason }}</td>
            <td class="highlight">{{ r.would_send?.join(' / ') || r.understanding || '—' }}</td>
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
  color: var(--text);
  font-weight: 500;
  max-width: 320px;
}

.highlight {
  color: var(--accent-strong);
  font-weight: 500;
}
</style>
