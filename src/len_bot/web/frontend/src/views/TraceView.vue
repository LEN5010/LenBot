<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const traces = ref([])
const selected = ref(null)
const filters = ref({ scene_id: '', kind: '' })
const error = ref('')

onMounted(load)
async function load() {
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (filters.value.scene_id) params.set('scene_id', filters.value.scene_id)
    if (filters.value.kind) params.set('kind', filters.value.kind)
    params.set('limit', '100')
    traces.value = await api('/api/cockpit/traces?' + params.toString())
  } catch (e) { error.value = e.message }
}

function kindLabel(kind) { return kind === 'social_cognition_error' ? '处理失败' : '社交判断' }
function decisionLabel(action) { return action === 'speak' ? '准备发言' : action === 'silence' ? '选择沉默' : '未完成' }
function toolCount(trace) { return (trace.payload?.cognition?.steps || []).reduce((count, step) => count + (step.tool_calls?.length || 0), 0) }
function messages(trace) { return (trace.payload?.result?.message_proposals || []).map(item => item.content).join(' / ') }
</script>

<template>
  <div class="trace-view">
    <div class="toolbar">
      <div class="page-title"><h1>机器人动态</h1><p class="muted">查看它刚才看懂了什么、为什么开口或沉默。</p></div>
      <button @click="load">刷新</button>
    </div>

    <div class="toolbar filter-bar">
      <input v-model="filters.scene_id" placeholder="输入群聊标识" />
      <select v-model="filters.kind"><option value="">全部动态</option><option value="social_cognition">社交判断</option><option value="social_cognition_error">处理失败</option></select>
      <button @click="load">筛选</button>
    </div>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <div class="activity-list">
      <article v-for="trace in traces" :key="trace.id" class="activity-card" :class="{ selected: selected?.id === trace.id }" @click="selected = trace">
        <div class="activity-time">{{ fmtTime(trace.created_at) }}</div>
        <div class="activity-main">
          <div class="activity-title">
            <span :class="trace.kind === 'social_cognition_error' ? 'tag bad' : 'tag ok'">{{ kindLabel(trace.kind) }}</span>
            <strong>{{ trace.payload?.burst?.text || trace.payload?.error || '没有文本内容' }}</strong>
          </div>
          <p v-if="trace.payload?.result">{{ trace.payload.result.perception?.summary || trace.payload.result.decision?.reason }}</p>
          <p v-else class="bad-text">{{ trace.payload?.error }}</p>
        </div>
        <div class="activity-decision">
          <strong>{{ decisionLabel(trace.payload?.result?.decision?.action) }}</strong>
          <span v-if="toolCount(trace)">回忆了 {{ toolCount(trace) }} 次</span>
        </div>
      </article>
      <div v-if="!traces.length" class="panel empty">还没有机器人动态</div>
    </div>

    <div v-if="selected" class="panel detail-panel">
      <div class="panel-header"><div><div class="bento-badge">这次判断的详情</div><h2>{{ decisionLabel(selected.payload?.result?.decision?.action) }}</h2></div><button class="small-btn" @click="selected = null">关闭</button></div>

      <div v-if="selected.payload?.result" class="bento-grid">
        <div class="bento-card bento-col-6">
          <div class="bento-badge">它看懂了什么</div>
          <p class="detail-copy">{{ selected.payload.result.perception?.summary || '没有留下理解摘要' }}</p>
          <div class="kv"><span class="k">当时收到的内容</span><span class="v">{{ selected.payload.burst?.text || '—' }}</span></div>
          <div class="kv"><span class="k">是否使用历史记忆</span><span class="v">{{ toolCount(selected) ? `是，共 ${toolCount(selected)} 次` : '否' }}</span></div>
        </div>
        <div class="bento-card bento-col-6">
          <div class="bento-badge">为什么这样决定</div>
          <p class="detail-copy">{{ selected.payload.result.decision?.reason || '没有填写原因' }}</p>
          <div class="kv"><span class="k">准备发送</span><span class="v highlight">{{ messages(selected) || '不发送消息' }}</span></div>
          <div class="kv"><span class="k">系统是否允许</span><span class="v">{{ selected.payload.gate?.accepted === false ? '已阻止' : selected.payload.gate ? '已允许' : '无需发送' }}</span></div>
        </div>
      </div>

      <details><summary>查看原始记录</summary><pre>{{ JSON.stringify(selected.payload, null, 2) }}</pre></details>
    </div>
  </div>
</template>

<style scoped>
.activity-list { display: grid; gap: 11px; }
.activity-card { padding: 15px 17px; display: grid; grid-template-columns: 130px minmax(0, 1fr) 120px; gap: 16px; align-items: center; cursor: pointer; background: rgba(255,255,255,.72); border: 1px solid rgba(255,255,255,.9); border-radius: 15px; box-shadow: var(--shadow-sm); transition: .18s ease; }
.activity-card:hover, .activity-card.selected { transform: translateY(-1px); border-color: var(--border-accent); background: rgba(255,255,255,.93); }
.activity-time { color: var(--muted); font-size: .8rem; }
.activity-title { display: flex; align-items: center; gap: 9px; }
.activity-title strong { overflow: hidden; color: var(--text); font-size: .91rem; text-overflow: ellipsis; white-space: nowrap; }
.activity-main p { margin: 6px 0 0; color: var(--muted); font-size: .82rem; line-height: 1.45; }
.activity-decision { text-align: right; }
.activity-decision strong, .activity-decision span { display: block; }
.activity-decision strong { color: var(--text-soft); font-size: .86rem; }
.activity-decision span { margin-top: 4px; color: var(--accent); font-size: .75rem; }
.detail-panel { margin-top: 20px; }
.bento-col-6 { grid-column: span 6; }
.detail-copy { min-height: 50px; color: var(--text-soft); line-height: 1.65; }
details { color: var(--muted); font-size: .84rem; }
summary { cursor: pointer; }
.empty { color: var(--muted); text-align: center; }
@media (max-width: 760px) { .activity-card { grid-template-columns: 1fr; } .activity-time, .activity-decision { text-align: left; } .bento-col-6 { grid-column: span 12; } }
</style>
