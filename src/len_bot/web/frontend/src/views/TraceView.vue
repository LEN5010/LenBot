<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const traces = ref([])
const selected = ref(null)
const filters = ref({ scene_id: '', kind: '' })
const error = ref('')

onMounted(load)
async function load() {
  try {
    const params = new URLSearchParams()
    if (filters.value.scene_id) params.set('scene_id', filters.value.scene_id)
    if (filters.value.kind) params.set('kind', filters.value.kind)
    params.set('limit', '100')
    traces.value = await api('/api/cockpit/traces?' + params.toString())
  } catch (e) { error.value = e.message }
}

const selectedDetail = ref(null)
function openTrace(t) {
  selected.value = t
  selectedDetail.value = JSON.stringify(t.payload, null, 2)
}
</script>

<template>
  <div>
    <h1>Trace</h1>
    <p class="muted">每条 Stimulus / Episode 的完整因果链：为什么插嘴、为什么沉默。</p>
    <div class="toolbar">
      <input v-model="filters.scene_id" placeholder="scene_id 精确过滤" />
      <select v-model="filters.kind">
        <option value="">全部类型</option>
        <option value="attention">attention</option>
        <option value="episode">episode</option>
      </select>
      <button @click="load">查询</button>
    </div>

    <div class="panel">
      <table>
        <thead><tr><th>时间</th><th>Kind</th><th>Scene</th><th>Ref</th><th></th></tr></thead>
        <tbody>
          <tr v-for="t in traces" :key="t.id">
            <td>{{ fmtTime(t.created_at) }}</td>
            <td><span class="tag" :class="t.kind === 'episode' ? 'warn' : 'ok'">{{ t.kind }}</span></td>
            <td>{{ t.scene_id }}</td>
            <td class="muted">{{ t.ref_id }}</td>
            <td><button @click="openTrace(t)">查看</button></td>
          </tr>
          <tr v-if="!traces.length"><td colspan="5" class="muted">暂无 trace 记录</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="selected" class="panel">
      <h3 style="margin-top:0">Trace {{ selected.id }} ({{ selected.kind }})</h3>
      <template v-if="selected.kind === 'episode' && selected.payload">
        <div class="kv"><span class="k">Stimulus</span><span>{{ selected.payload.stimulus?.actor_id }}: {{ selected.payload.stimulus?.text }}</span></div>
        <div class="kv"><span class="k">Attention</span><span>{{ selected.payload.attention?.disposition }} ({{ selected.payload.attention?.reason }})</span></div>
        <div class="kv"><span class="k">Cognition</span><span>{{ selected.payload.cognition?.mode }} · {{ selected.payload.cognition?.steps?.length }} steps · interim {{ selected.payload.cognition?.interim_injections }}</span></div>
        <div class="kv" v-for="(s, i) in selected.payload.cognition?.steps || []" :key="i">
          <span class="k">Step {{ s.step }}</span>
          <span>{{ s.tier }}/{{ s.model }}<span v-if="s.tool_calls?.length"> · tools: {{ s.tool_calls.map(tc => tc.name).join(', ') }}</span></span>
        </div>
        <div class="kv"><span class="k">Outcome</span><span>{{ selected.payload.outcome?.disposition }} · {{ selected.payload.outcome?.thought }}</span></div>
        <div class="kv"><span class="k">Gate</span><span>{{ selected.payload.gate?.disposition }} · {{ selected.payload.gate?.reason }}</span></div>
        <div class="kv"><span class="k">Durable effects</span>
          <span>tasks {{ selected.payload.durable_effects?.tasks?.length }} · loops {{ selected.payload.durable_effects?.resolved_loops?.length }} · memories {{ selected.payload.durable_effects?.memories?.length }} · actions {{ selected.payload.actions_enqueued }}</span></div>
      </template>
      <pre>{{ selectedDetail }}</pre>
    </div>
  </div>
</template>
