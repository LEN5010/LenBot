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
  } catch (e) {
    error.value = e.message
  }
}

async function openChain(m) {
  error.value = ''
  try {
    chain.value = (await api(`/api/cockpit/memories/${m.id}/chain`)).chain
  } catch (e) {
    error.value = e.message
  }
}

async function act(m, action) {
  if (action === 'refute' && !confirm('确认驳斥并作废该记忆信念？')) return
  try {
    await api(`/api/cockpit/memories/${m.id}/${action}`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'manual' })
    })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function promote(m) {
  if (!confirm(`确认提权该条记忆至全域安全可见 (scope=global-safe)？\n"${m.human_readable_assertion}"`)) return
  try {
    await api(`/api/cockpit/memories/${m.id}/promote`, { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="memory-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>社会记忆信念库 (Social Memory)</h1>
        <p class="muted">typed 社会信念（L2）。不可篡改事件溯源证据链与执行域 (Scope) 隔离门禁</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新记忆</span>
      </button>
    </div>

    <div class="toolbar filter-bar">
      <input v-model="filters.subject" placeholder="过滤主体 如 user:1001..." />
      <input v-model="filters.scope" placeholder="过滤场景 如 group:123..." />
      <select v-model="filters.status">
        <option value="">全部信念状态</option>
        <option value="active">正常生效 (active)</option>
        <option value="superseded">演化取代 (superseded)</option>
        <option value="refuted">已被驳斥 (refuted)</option>
        <option value="forgotten">时间衰减遗忘 (forgotten)</option>
      </select>
      <button @click="load">筛选</button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>记忆主体 (Subject)</th>
            <th>类型 (Kind)</th>
            <th>槽位属性 (Key)</th>
            <th>人类可读断言 (Assertion)</th>
            <th>确定性</th>
            <th>安全域 (Scope)</th>
            <th>状态</th>
            <th>证据链溯源</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in memories" :key="m.id">
            <td><code>{{ m.subject }}</code></td>
            <td><span class="tag">{{ m.kind }}</span></td>
            <td><span class="muted">{{ m.key }}</span></td>
            <td class="assertion-cell">{{ m.human_readable_assertion }}</td>
            <td>
              <span class="tag" :class="m.certainty === 'confirmed' ? 'ok' : 'warn'">
                {{ m.certainty }}
              </span>
            </td>
            <td>
              <span class="tag" :class="m.scope === 'global-safe' ? 'ok' : ''">
                {{ m.scope }}
              </span>
            </td>
            <td>
              <span class="tag" :class="m.status === 'active' ? 'ok' : (m.status === 'superseded' ? 'warn' : 'bad')">
                {{ m.status }}
              </span>
              <span v-if="m.superseded_by" class="muted" style="margin-left: 4px;">
                → {{ m.superseded_by.slice(0, 8) }}
              </span>
            </td>
            <td><span class="tag">{{ m.evidence.length }} 证据</span></td>
            <td>
              <div class="action-btn-group">
                <button class="small-btn" @click="openChain(m)">演化链</button>
                <button v-if="m.status === 'active' && m.scope !== 'global-safe'" class="small-btn primary" @click="promote(m)">提权</button>
                <button v-if="m.status === 'active'" class="small-btn danger" @click="act(m, 'refute')">驳斥</button>
              </div>
            </td>
          </tr>
          <tr v-if="!memories.length">
            <td colspan="9" class="muted" style="text-align: center; padding: 24px;">暂无匹配的社会记忆信念</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Belief Evolution Chain Bento Panel -->
    <div v-if="chain.length" class="panel detail-panel">
      <div class="chain-header">
        <h2>信念演化历史链条 (Evolution Chain · 最早 → 最新)</h2>
        <button class="small-btn" @click="chain = []">关闭</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>信念状态</th>
            <th>演化断言</th>
            <th>确定性</th>
            <th>生效安全域</th>
            <th>沉淀时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in chain" :key="m.id">
            <td>
              <span class="tag" :class="m.status === 'active' ? 'ok' : 'warn'">{{ m.status }}</span>
            </td>
            <td class="assertion-cell">{{ m.human_readable_assertion }}</td>
            <td>{{ m.certainty }}</td>
            <td><code>{{ m.scope }}</code></td>
            <td>{{ fmtTime(m.created_at) }}</td>
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

.filter-bar {
  margin-top: 14px;
}

.assertion-cell {
  color: #f1f5f9;
  font-weight: 500;
  max-width: 320px;
}

.action-btn-group {
  display: flex;
  gap: 6px;
  align-items: center;
}

.small-btn {
  padding: 3px 8px;
  font-size: 0.78rem;
}

.chain-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.chain-header h2 {
  margin: 0;
}
</style>
