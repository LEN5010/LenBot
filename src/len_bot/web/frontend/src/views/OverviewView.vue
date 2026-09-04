<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const error = ref('')
onMounted(load)
async function load() { try { data.value = await api('/api/overview/stats'); error.value = '' } catch (e) { error.value = e.message } }
function activityLabel(value) { return value === 'HIGH' ? '很活跃' : value === 'MEDIUM' ? '有消息' : '安静' }
function engagementLabel(value) { return value === 'participating' || value === 'active' ? '正在参与' : value === 'lightly_participating' ? '偶尔参与' : '正在旁观' }
</script>

<template>
  <div class="overview-view">
    <div class="toolbar">
      <div class="page-title"><h1>今天运行得怎么样</h1><p class="muted">先看连接、发送状态和最近群聊，其他数字需要时再展开。</p></div>
      <button @click="load">刷新</button>
    </div>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <template v-if="data">
      <div class="bento-grid">
        <div class="bento-card bento-col-4 primary-card">
          <div class="bento-badge">QQ 连接</div>
          <div class="hero-status"><span :class="data.stats.websocket_connected ? 'big-dot online' : 'big-dot'"></span>{{ data.stats.websocket_connected ? '已经连接' : '等待连接' }}</div>
          <div class="bento-desc">{{ data.stats.websocket_connected ? '可以正常接收群消息' : '请检查 QQ 端的反向连接设置' }}</div>
        </div>
        <div class="bento-card bento-col-4">
          <div class="bento-badge">消息发送</div>
          <div class="hero-status">{{ data.stats.shadow_mode ? '只观察，不实发' : '正在真实发送' }}</div>
          <div class="bento-desc">{{ data.stats.shadow_mode ? '机器人会正常思考，但消息只记在后台' : '机器人通过审核的消息会发到 QQ' }}</div>
        </div>
        <div class="bento-card bento-col-4">
          <div class="bento-badge">当前聊天模型</div>
          <div class="hero-model">{{ data.stats.normal_model || '尚未设置' }}</div>
          <div class="bento-desc">复杂问题会切换到 {{ data.stats.deliberate_model || '尚未设置的思考模型' }}</div>
        </div>
        <div class="bento-card bento-col-7">
          <div class="bento-badge">今天的互动</div>
          <div class="stats-row"><div><strong>{{ data.social_metrics.human_messages }}</strong><span>收到消息</span></div><div><strong>{{ data.social_metrics.social_cognition }}</strong><span>理解场景</span></div><div><strong>{{ data.social_metrics.intentional_silence }}</strong><span>看懂后没插嘴</span></div><div><strong>{{ data.social_metrics.visible_messages }}</strong><span>实际发言</span></div></div>
        </div>
        <div class="bento-card bento-col-5">
          <div class="bento-badge">持续记忆</div>
          <div class="bento-hero-stat">{{ data.stats.memory_beliefs_count }}<span class="unit">条</span></div>
          <div class="bento-desc">已经保存的人物、关系、偏好和群体习惯</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header"><div><h2>最近的群聊</h2><p class="muted">点开“群聊”页面可以查看更完整的上下文。</p></div><span class="tag">{{ data.scenes.length }} 个</span></div>
        <table>
          <thead><tr><th>群聊</th><th>现在是否活跃</th><th>大家在聊什么</th><th>机器人状态</th></tr></thead>
          <tbody>
            <tr v-for="scene in data.scenes" :key="scene.scene_id"><td><code>{{ scene.scene_id }}</code></td><td>{{ activityLabel(scene.activity) }}</td><td>{{ scene.topics?.map(topic => topic.subject).join(' / ') || '还没有明确话题' }}</td><td>{{ engagementLabel(scene.engagement) }}</td></tr>
            <tr v-if="!data.scenes.length"><td colspan="4" class="muted empty">还没有收到群聊消息</td></tr>
          </tbody>
        </table>
      </div>

      <details class="panel"><summary>查看运行明细</summary><div class="detail-grid"><span>累计事件 {{ data.stats.total_events }}</span><span>等待执行 {{ data.stats.pending_tasks }}</span><span>等待回复 {{ data.stats.active_open_loops }}</span><span>已运行 {{ Math.floor(data.stats.uptime_seconds / 60) }} 分钟</span></div></details>
    </template>
  </div>
</template>

<style scoped>
.bento-col-4 { grid-column: span 4; }.bento-col-5 { grid-column: span 5; }.bento-col-7 { grid-column: span 7; }
.primary-card { background: linear-gradient(145deg, rgba(237,246,255,.9), rgba(255,255,255,.78)); }
.hero-status { margin: 15px 0 11px; display: flex; align-items: center; gap: 12px; color: var(--text); font-size: 1.45rem; font-weight: 780; letter-spacing: -.03em; }
.big-dot { width: 13px; height: 13px; border-radius: 50%; background: #cbd5e1; box-shadow: 0 0 0 6px rgba(148,163,184,.13); }.big-dot.online { background: #10b981; box-shadow: 0 0 0 6px rgba(16,185,129,.12); }
.hero-model { margin: 15px 0 11px; color: var(--text); font-size: 1.3rem; font-weight: 760; overflow-wrap: anywhere; }
.stats-row { height: 100%; display: grid; grid-template-columns: repeat(4, 1fr); align-items: center; gap: 12px; }.stats-row div { padding-right: 12px; border-right: 1px solid var(--border); }.stats-row div:last-child { border: 0; }.stats-row strong, .stats-row span { display: block; }.stats-row strong { color: var(--text); font-size: 1.8rem; }.stats-row span { margin-top: 4px; color: var(--muted); font-size: .78rem; }
.unit { margin-left: 5px; color: var(--muted); font-size: 1rem; }.empty { padding: 28px; text-align: center; }.detail-grid { margin-top: 16px; display: flex; gap: 24px; flex-wrap: wrap; color: var(--muted); font-size: .86rem; }summary { color: var(--text); font-weight: 700; cursor: pointer; }
@media (max-width: 980px) { .bento-col-4, .bento-col-5, .bento-col-7 { grid-column: span 12; } }@media (max-width: 620px) { .stats-row { grid-template-columns: 1fr 1fr; }.stats-row div { border: 0; } }
</style>
