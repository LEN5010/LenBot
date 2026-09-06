<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const memories = ref([]), filters = ref({ subject: '', scope: '', status: 'active' })
const chain = ref([]), chainId = ref(''), error = ref(''), message = ref('')
const refuting = ref(null), reason = ref(''), saving = ref(false)
const kindLabels = { address: '称呼', preference: '偏好', relationship: '关系', fact: '事实', group_norm: '群体规范' }
const statusLabels = { active: '有效', superseded: '已替代', refuted: '已撤销' }
onMounted(load)
async function load() {
  error.value = ''
  try {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters.value)) if (value.trim()) params.set(key, value.trim())
    memories.value = await api('/api/cockpit/memories?' + params.toString())
  } catch (e) { error.value = e.message }
}
async function openChain(memory) {
  error.value = ''; chainId.value = memory.id
  try { chain.value = (await api(`/api/cockpit/memories/${encodeURIComponent(memory.id)}/chain`)).chain }
  catch (e) { error.value = e.message }
}
function beginRefute(memory) { refuting.value = memory; reason.value = ''; message.value = '' }
async function refute() {
  if (!reason.value.trim()) return
  error.value = ''; saving.value = true
  try {
    const id = refuting.value.id
    await api(`/api/cockpit/memories/${encodeURIComponent(id)}/refute`, { method: 'POST', body: JSON.stringify({ reason: reason.value.trim() }) })
    refuting.value = null; message.value = '认识已撤销，原记录和修订依据仍然保留'
    await load()
    if (chainId.value === id) await openChain({ id })
  } catch (e) { error.value = e.message } finally { saving.value = false }
}
function basisLabel(basis) { return basis === 'reported' ? '原话报告' : '有据推断' }
function expired(memory) { return memory.expires_at != null && memory.expires_at <= Date.now() / 1000 }
</script>

<template>
  <div class="memory-view">
    <div class="toolbar"><div class="page-title"><h1>认识与记忆</h1><p class="muted">区分群友原话和模型推断，查看来源、适用时间及修订。</p></div><button @click="load">刷新</button></div>
    <form class="toolbar filter-bar" @submit.prevent="load"><input v-model="filters.subject" placeholder="对象，例如 user:1001" /><input v-model="filters.scope" placeholder="场景，例如 group:123" /><select v-model="filters.status"><option value="">全部状态</option><option value="active">有效</option><option value="superseded">已替代</option><option value="refuted">已撤销</option></select><button>筛选</button></form>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p><p v-if="message" class="tag ok" role="status">{{ message }}</p>
    <section v-if="refuting" class="panel refute-panel"><h2>撤销这条认识</h2><p class="statement">{{ refuting.statement }}</p><form @submit.prevent="refute"><label>撤销依据<textarea v-model="reason" rows="3" required placeholder="说明哪里不准确，以及已确认的纠正信息" /></label><div class="button-row"><button class="danger" :disabled="saving || !reason.trim()">{{ saving ? '正在撤销…' : '记录依据并撤销' }}</button><button type="button" :disabled="saving" @click="refuting = null">取消</button></div></form></section>
    <section class="panel table-scroll"><table><thead><tr><th>对象与类型</th><th>认识内容</th><th>依据性质</th><th>使用范围</th><th>状态与到期</th><th>来源</th><th>操作</th></tr></thead><tbody>
      <tr v-for="memory in memories" :key="memory.id"><td><code>{{ memory.subject }}</code><p class="muted">{{ kindLabels[memory.kind] || memory.kind }}</p></td><td class="statement">{{ memory.statement }}</td><td><span class="tag" :class="memory.basis === 'reported' ? 'ok' : 'warn'">{{ basisLabel(memory.basis) }}</span></td><td><code>{{ memory.scope }}</code></td><td><span class="tag" :class="memory.status === 'active' && !expired(memory) ? 'ok' : 'warn'">{{ statusLabels[memory.status] || memory.status }}</span><p class="muted">{{ memory.expires_at ? fmtTime(memory.expires_at) : '未设到期时间' }}</p><span v-if="expired(memory)" class="tag warn">已过期</span></td><td><details><summary>{{ memory.evidence?.length || 0 }} 条证据</summary><p v-for="id in memory.evidence || []" :key="id"><code>{{ id }}</code></p></details></td><td><div class="action-buttons"><button class="small-btn" @click="openChain(memory)">查看修订</button><button v-if="memory.status === 'active'" class="small-btn danger" @click="beginRefute(memory)">撤销</button></div></td></tr>
      <tr v-if="!memories.length"><td colspan="7" class="empty muted">暂无匹配的认识</td></tr>
    </tbody></table></section>
    <section v-if="chain.length" class="panel"><div class="panel-header"><h2>修订记录</h2><button @click="chain = []; chainId = ''">关闭</button></div><article v-for="memory in chain" :key="memory.id" class="revision"><div class="revision-meta"><span class="tag">{{ statusLabels[memory.status] || memory.status }}</span><span>{{ basisLabel(memory.basis) }}</span><time>{{ fmtTime(memory.created_at) }}</time></div><p class="statement">{{ memory.statement }}</p><p v-if="memory.revision_reason">修订依据：{{ memory.revision_reason }}</p><details><summary>来源与修订标识</summary><p>记录：{{ memory.id }}</p><p>原始证据：{{ memory.evidence?.join('、') || '—' }}</p><p v-if="memory.revision_evidence?.length">修订证据：{{ memory.revision_evidence.join('、') }}</p><p v-if="memory.supersedes_ids?.length">替代记录：{{ memory.supersedes_ids.join('、') }}</p><p v-if="memory.superseded_by">后续记录：{{ memory.superseded_by }}</p></details></article></section>
  </div>
</template>

<style scoped>
.filter-bar { margin-top:14px }.table-scroll { overflow-x:auto }.statement { color:var(--text);line-height:1.65;min-width:200px;max-width:460px;white-space:pre-wrap;overflow-wrap:anywhere }.action-buttons,.button-row { display:flex;gap:8px;flex-wrap:wrap }.small-btn { padding:5px 9px;font-size:.78rem }.empty { padding:28px;text-align:center }details { font-size:.8rem;color:var(--muted) }summary { cursor:pointer }details p { overflow-wrap:anywhere }.refute-panel { border-color:var(--border-accent) }.refute-panel label { display:flex;flex-direction:column;gap:9px }.refute-panel textarea { width:100%;margin-bottom:12px }.revision { padding:15px 0;border-bottom:1px solid var(--border) }.revision:last-child { border:0 }.revision-meta { display:flex;gap:12px;align-items:center;color:var(--muted);font-size:.8rem;flex-wrap:wrap }
</style>
