<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const memories = ref([])
const filters = ref({ subject: '', scope: '', status: 'active' })
const chain = ref([])
const error = ref('')

onMounted(load)
async function load() {
  try {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(filters.value)) if (v) params.set(k, v)
    params.set('limit', '100')
    memories.value = await api('/api/cockpit/memories?' + params.toString())
  } catch (e) { error.value = e.message }
}

async function openChain(m) {
  error.value = ''
  try { chain.value = (await api(`/api/cockpit/memories/${m.id}/chain`)).chain } catch (e) { error.value = e.message }
}

async function act(m, action) {
  try {
    await api(`/api/cockpit/memories/${m.id}/${action}`, { method: 'POST', body: JSON.stringify({ reason: 'manual' }) })
    await load()
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <h1>Memory Explorer</h1>
    <p class="muted">typed 社会信念（L2）。evidence 可溯源到原始事件；scope 决定可见边界。</p>
    <div class="toolbar">
      <input v-model="filters.subject" placeholder="subject 如 user:1001" />
      <input v-model="filters.scope" placeholder="scope 如 group:123" />
      <select v-model="filters.status">
        <option value="">全部状态</option>
        <option value="active">active</option>
        <option value="superseded">superseded</option>
        <option value="refuted">refuted</option>
        <option value="forgotten">forgotten</option>
      </select>
      <button @click="load">查询</button>
    </div>

    <div class="panel">
      <table>
        <thead><tr><th>Subject</th><th>Kind</th><th>Key</th><th>断言</th><th>确定性</th><th>Scope</th><th>状态</th><th>Evidence</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="m in memories" :key="m.id">
            <td>{{ m.subject }}</td>
            <td><span class="tag">{{ m.kind }}</span></td>
            <td>{{ m.key }}</td>
            <td>{{ m.human_readable_assertion }}</td>
            <td>{{ m.certainty }}</td>
            <td>{{ m.scope }} <span class="muted">({{ m.visibility }})</span></td>
            <td>
              <span class="tag" :class="m.status === 'active' ? 'ok' : (m.status === 'superseded' ? 'warn' : 'bad')">{{ m.status }}</span>
              <span v-if="m.superseded_by" class="muted">→ {{ m.superseded_by.slice(0, 10) }}</span>
            </td>
            <td><span class="muted">{{ m.evidence.length }} 条</span></td>
            <td>
              <button @click="openChain(m)">链</button>
              <button v-if="m.status === 'active'" class="danger" @click="act(m, 'refute')">驳斥</button>
            </td>
          </tr>
          <tr v-if="!memories.length"><td colspan="9" class="muted">无记忆记录</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="chain.length" class="panel">
      <h3 style="margin-top:0">信念演化链 (oldest → newest)</h3>
      <table>
        <thead><tr><th>状态</th><th>断言</th><th>确定性</th><th>创建</th></tr></thead>
        <tbody>
          <tr v-for="m in chain" :key="m.id">
            <td><span class="tag" :class="m.status === 'active' ? 'ok' : 'warn'">{{ m.status }}</span></td>
            <td>{{ m.human_readable_assertion }}</td>
            <td>{{ m.certainty }}</td>
            <td>{{ fmtTime(m.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
