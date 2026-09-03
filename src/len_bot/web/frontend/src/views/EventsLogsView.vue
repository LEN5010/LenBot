<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const events = ref([])
const logs = ref([])
const filters = ref({ scene_id: '', actor_id: '', event_type: '' })
const logLevel = ref('')
const error = ref('')

const EVENT_TYPES = [
  'GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED', 'MESSAGE_SENT', 'MESSAGE_SEND_FAILED',
  'TASK_DUE', 'STATE_ANNOTATION', 'LIVE_STARTED', 'LIVE_ENDED', 'TOOL_COMPLETED', 'USER_JOINED',
]

onMounted(load)
async function load() {
  try {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(filters.value)) if (v) params.set(k, v)
    params.set('limit', '80')
    events.value = await api('/api/cockpit/events?' + params.toString())
    logs.value = await api('/api/logs?' + new URLSearchParams(logLevel.value ? { level: logLevel.value } : {}))
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="events-logs-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>事件总线与系统日志 (Events & Logs)</h1>
        <p class="muted">Events 是不可篡改的领域事实；Logs 是内存环形运维排查日志</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新数据</span>
      </button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <!-- Events Panel -->
    <div class="panel">
      <div class="panel-header">
        <h2>领域事实事件总线 (Immutable Event Bus)</h2>
        <span class="tag ok">最近 {{ events.length }} 条记录</span>
      </div>

      <div class="toolbar filter-bar">
        <input v-model="filters.scene_id" placeholder="过滤场景 如 group:123..." />
        <input v-model="filters.actor_id" placeholder="过滤主体 如 user:1001..." />
        <select v-model="filters.event_type">
          <option value="">全部事件类型</option>
          <option v-for="t in EVENT_TYPES" :key="t" :value="t">{{ t }}</option>
        </select>
        <button @click="load">筛选事件</button>
      </div>

      <table>
        <thead>
          <tr>
            <th>事件时间</th>
            <th>事件类型</th>
            <th>发生场景</th>
            <th>发起主体</th>
            <th>事件载荷内容</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td>{{ fmtTime(e.timestamp) }}</td>
            <td><span class="tag">{{ e.event_type }}</span></td>
            <td><code>{{ e.scene_id }}</code></td>
            <td><code>{{ e.actor_id }}</code></td>
            <td class="payload-cell">{{ e.payload?.raw_text || e.payload?.content || '—' }}</td>
          </tr>
          <tr v-if="!events.length">
            <td colspan="5" class="muted" style="text-align: center; padding: 24px;">暂无匹配的事实事件</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Logs Panel -->
    <div class="panel" style="margin-top: 24px;">
      <div class="panel-header">
        <h2>系统运行日志缓冲 (In-Memory Log Ring)</h2>
        <div class="toolbar" style="margin: 0;">
          <select v-model="logLevel">
            <option value="">全部日志级别</option>
            <option value="INFO">INFO (信息)</option>
            <option value="WARNING">WARNING (警告)</option>
            <option value="ERROR">ERROR (异常)</option>
          </select>
          <button class="small-btn" @click="load">过滤日志</button>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>记录时间</th>
            <th>日志级别</th>
            <th>来源组件</th>
            <th>日志详细信息</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(l, i) in logs" :key="i">
            <td>{{ fmtTime(l.timestamp) }}</td>
            <td>
              <span class="tag" :class="l.level === 'ERROR' ? 'bad' : (l.level === 'WARNING' ? 'warn' : 'ok')">
                {{ l.level }}
              </span>
            </td>
            <td class="muted"><code>{{ l.component }}</code></td>
            <td class="log-message-cell">{{ l.message }}</td>
          </tr>
          <tr v-if="!logs.length">
            <td colspan="4" class="muted" style="text-align: center; padding: 24px;">暂无系统运维日志</td>
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

.filter-bar {
  margin-bottom: 14px;
}

.small-btn {
  padding: 4px 10px;
  font-size: 0.82rem;
}

.payload-cell {
  color: #e2e8f0;
  font-weight: 500;
  max-width: 400px;
}

.log-message-cell {
  font-family: monospace;
  font-size: 0.84rem;
  color: #cbd5e1;
}
</style>
