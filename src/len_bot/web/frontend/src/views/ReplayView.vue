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
    try { overrides = JSON.parse(overridesText.value) } catch { throw new Error('overrides 不是合法 JSON') }
    const body = {
      scene_id: sceneId.value,
      since: since.value ? Number(since.value) : null,
      until: until.value ? Number(until.value) : null,
      overrides,
    }
    const res = await api('/api/replay', { method: 'POST', body: JSON.stringify(body) })
    runs.value = res.runs
  } catch (e) { error.value = e.message } finally { busy.value = false }
}

const DISPOSITION_CLASS = { wake: 'warn', observe: '', track: 'ok' }
</script>

<template>
  <div>
    <h1>Replay Lab</h1>
    <p class="muted">用录制的事件窗口离线回放 Attention/Cognition 策略，输出逐条 disposition，可对比多组策略参数。</p>

    <div class="panel">
      <div class="toolbar">
        <input v-model="sceneId" placeholder="scene_id 如 group:123" style="min-width:220px" />
        <input v-model="since" placeholder="since (unix 秒, 可空)" />
        <input v-model="until" placeholder="until (unix 秒, 可空)" />
        <button class="primary" :disabled="busy || !sceneId" @click="run">{{ busy ? '回放中…' : '回放' }}</button>
      </div>
      <label class="muted" style="display:block; margin-bottom:4px">Policy overrides(数组，每个元素是一组策略)</label>
      <textarea v-model="overridesText" rows="5" style="width:100%; font-family:inherit"></textarea>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <div v-for="run in runs" :key="run.policy" class="panel">
      <h3 style="margin-top:0">{{ run.policy }}
        <span class="muted">messages {{ run.summary.messages }} · wake {{ run.summary.wake }}</span></h3>
      <table>
        <thead><tr><th>时间</th><th>Actor</th><th>消息</th><th>Disposition</th><th>Reason</th><th>Cognition</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in run.rows" :key="i">
            <td>{{ new Date(r.timestamp * 1000).toLocaleTimeString() }}</td>
            <td>{{ r.actor_id }}</td>
            <td>{{ r.text }}</td>
            <td><span class="tag" :class="DISPOSITION_CLASS[r.disposition] || ''">{{ r.disposition }}</span></td>
            <td class="muted">{{ r.reason }}</td>
            <td>{{ r.cognition || '—' }}</td>
          </tr>
          <tr v-if="!run.rows.length"><td colspan="6" class="muted">无可回放消息</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
