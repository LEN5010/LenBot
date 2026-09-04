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
  if (action === 'refute' && !confirm('确认作废这条记忆？')) return
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
  if (!confirm(`确认让这条记忆可以在其他群聊中使用？\n"${m.human_readable_assertion}"`)) return
  try {
    await api(`/api/cockpit/memories/${m.id}/promote`, { method: 'POST' })
    await load()
  } catch (e) {
    error.value = e.message
  }
}

const KIND_LABELS = { preference: '偏好', habit: '习惯', relationship: '关系', fact: '事实', group_norm: '群规', topic_interest: '长期兴趣', recurring_role: '常见角色', social_pattern: '互动习惯' }
const STATUS_LABELS = { active: '正在使用', superseded: '已被更新', refuted: '已作废', forgotten: '已淡忘' }
const CERTAINTY_LABELS = { tentative: '不太确定', likely: '比较可信', strong: '很可信', explicit: '明确说过' }
function kindLabel(value) { return KIND_LABELS[value] || value }
function statusLabel(value) { return STATUS_LABELS[value] || value }
function certaintyLabel(value) { return CERTAINTY_LABELS[value] || value }
function scopeLabel(value) { return value === 'global-safe' ? '跨群可用' : '仅当前群' }
</script>

<template>
  <div class="memory-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>长期记忆</h1>
        <p class="muted">机器人从聊天中记住的人物、关系、偏好和群体习惯。</p>
      </div>
      <button class="primary" @click="load">
        <span>刷新</span>
      </button>
    </div>

    <div class="toolbar filter-bar">
      <input v-model="filters.subject" placeholder="按成员编号筛选，例如 user:1001" />
      <input v-model="filters.scope" placeholder="按群聊编号筛选，例如 group:123" />
      <select v-model="filters.status">
        <option value="">全部状态</option>
        <option value="active">正在使用</option>
        <option value="superseded">已被更新</option>
        <option value="refuted">已作废</option>
        <option value="forgotten">已淡忘</option>
      </select>
      <button @click="load">筛选</button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>关于谁</th>
            <th>记忆类型</th>
            <th>记忆事项</th>
            <th>记住的内容</th>
            <th>可信程度</th>
            <th>使用范围</th>
            <th>状态</th>
            <th>依据</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in memories" :key="m.id">
            <td><code>{{ m.subject }}</code></td>
            <td><span class="tag">{{ kindLabel(m.kind) }}</span></td>
            <td><span class="muted">{{ m.key }}</span></td>
            <td class="assertion-cell">{{ m.human_readable_assertion }}</td>
            <td>
              <span class="tag" :class="m.certainty === 'explicit' || m.certainty === 'strong' ? 'ok' : 'warn'">
                {{ certaintyLabel(m.certainty) }}
              </span>
            </td>
            <td>
              <span class="tag" :class="m.scope === 'global-safe' ? 'ok' : ''">
                {{ scopeLabel(m.scope) }}
              </span>
            </td>
            <td>
              <span class="tag" :class="m.status === 'active' ? 'ok' : (m.status === 'superseded' ? 'warn' : 'bad')">
                {{ statusLabel(m.status) }}
              </span>
              <span v-if="m.superseded_by" class="muted" style="margin-left: 4px;">
                → {{ m.superseded_by.slice(0, 8) }}
              </span>
            </td>
            <td><span class="tag">{{ m.evidence.length }} 证据</span></td>
            <td>
              <div class="action-btn-group">
                <button class="small-btn" @click="openChain(m)">查看变化</button>
                <button v-if="m.status === 'active' && m.scope !== 'global-safe'" class="small-btn primary" @click="promote(m)">允许跨群使用</button>
                <button v-if="m.status === 'active'" class="small-btn danger" @click="act(m, 'refute')">作废</button>
              </div>
            </td>
          </tr>
          <tr v-if="!memories.length">
            <td colspan="9" class="muted" style="text-align: center; padding: 24px;">暂无匹配的记忆</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Belief Evolution Chain Bento Panel -->
    <div v-if="chain.length" class="panel detail-panel">
      <div class="chain-header">
        <h2>这条记忆是怎样变化的</h2>
        <button class="small-btn" @click="chain = []">关闭</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>状态</th>
            <th>当时记住的内容</th>
            <th>可信程度</th>
            <th>使用范围</th>
            <th>记录时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in chain" :key="m.id">
            <td>
              <span class="tag" :class="m.status === 'active' ? 'ok' : 'warn'">{{ statusLabel(m.status) }}</span>
            </td>
            <td class="assertion-cell">{{ m.human_readable_assertion }}</td>
            <td>{{ certaintyLabel(m.certainty) }}</td>
            <td>{{ scopeLabel(m.scope) }}</td>
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
  color: var(--text);
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
