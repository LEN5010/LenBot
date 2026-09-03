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
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <h1>Events & Logs</h1>
    <p class="muted">Events 是不可变领域事实；Logs 是运行期运维日志，两者分开。</p>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <h2>Events (immutable domain facts)</h2>
    <div class="toolbar">
      <input v-model="filters.scene_id" placeholder="scene_id" />
      <input v-model="filters.actor_id" placeholder="actor_id" />
      <select v-model="filters.event_type">
        <option value="">全部类型</option>
        <option v-for="t in EVENT_TYPES" :key="t" :value="t">{{ t }}</option>
      </select>
      <button @click="load">查询</button>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>时间</th><th>Type</th><th>Scene</th><th>Actor</th><th>内容</th></tr></thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td>{{ fmtTime(e.timestamp) }}</td>
            <td><span class="tag">{{ e.event_type }}</span></td>
            <td>{{ e.scene_id }}</td>
            <td>{{ e.actor_id }}</td>
            <td>{{ e.payload?.raw_text || e.payload?.content || '—' }}</td>
          </tr>
          <tr v-if="!events.length"><td colspan="5" class="muted">无事件</td></tr>
        </tbody>
      </table>
    </div>

    <h2>Logs (operational)</h2>
    <div class="toolbar">
      <select v-model="logLevel">
        <option value="">全部级别</option>
        <option>INFO</option><option>WARNING</option><option>ERROR</option>
      </select>
      <button @click="load">刷新</button>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>时间</th><th>Level</th><th>Component</th><th>Message</th></tr></thead>
        <tbody>
          <tr v-for="(l, i) in logs" :key="i">
            <td>{{ fmtTime(l.timestamp) }}</td>
            <td><span class="tag" :class="l.level === 'ERROR' ? 'bad' : (l.level === 'WARNING' ? 'warn' : 'ok')">{{ l.level }}</span></td>
            <td class="muted">{{ l.component }}</td>
            <td>{{ l.message }}</td>
          </tr>
          <tr v-if="!logs.length"><td colspan="4" class="muted">无日志</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
