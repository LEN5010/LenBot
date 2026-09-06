<script setup>
import { computed, ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const scenes = ref([]), detail = ref(null), error = ref(''), loadingScene = ref('')
const participants = computed(() => Object.values(detail.value?.session?.participants || {}))
onMounted(load)
async function load() {
  error.value = ''
  try { scenes.value = (await api('/api/cockpit/scenes')).scenes }
  catch (e) { error.value = e.message }
}
async function openDetail(sceneId) {
  error.value = ''; loadingScene.value = sceneId
  try {
    const result = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
    if (loadingScene.value === sceneId) detail.value = result
  } catch (e) { error.value = e.message }
  finally { if (loadingScene.value === sceneId) loadingScene.value = '' }
}
function sender(event) {
  const participant = detail.value?.session?.participants?.[event.actor_id]
  return participant?.card || participant?.nickname || event.actor_id || '系统'
}
function eventText(event) { return event.raw_text || event.payload?.raw_text || event.payload?.content || '无文字内容' }
function basisLabel(value) { return value === 'reported' ? '原话报告' : '有据推断' }
function jobStatus(status) {
  return { pending: '等待执行', claimed: '已认领', processing: '正在处理', result_ready: '结果就绪', awaiting_delivery: '等待交付', completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', review_required: '等待核对', interrupted: '已中断' }[status] || status
}
function deliveryLabel(event) {
  if (event.event_type === 'ACTION_SHADOWED') return 'Shadow · 未实发'
  return { sent: '已送达', not_sent: '未发出', rejected: '已拒绝', unknown: '结果不确定' }[event.payload?.delivery_status]
    || (event.event_type === 'MESSAGE_SENT' ? '已送达' : '未确认送达')
}
</script>

<template>
  <div class="scenes-view">
    <div class="toolbar"><div class="page-title"><h1>群聊状态</h1><p class="muted">查看事件形成的场景事实、正在进行的工作和真实发送回执。</p></div><button @click="load">刷新</button></div>
    <p v-if="error" class="tag bad" role="alert">{{ error }}</p>
    <div class="grid cards">
      <button v-for="scene in scenes" :key="scene.scene_id" type="button" class="card scene-card" :class="{ selected: detail?.session?.scene_id === scene.scene_id }" @click="openDetail(scene.scene_id)">
        <div class="scene-header"><h3><code>{{ scene.scene_id }}</code></h3><span class="tag">版本 {{ scene.version }}</span></div>
        <div class="kv"><span class="k">已记录成员</span><span class="v">{{ scene.participant_count }} 人</span></div>
        <div class="kv"><span class="k">最近事件</span><span class="v">{{ fmtTime(scene.last_event_at) }}</span></div>
        <div class="kv"><span class="k">最近实际发言</span><span class="v">{{ fmtTime(scene.last_bot_message_at) }}</span></div>
        <div class="kv"><span class="k">进行中的工作</span><span class="v highlight">{{ scene.active_job_count }}</span></div>
      </button>
      <p v-if="!scenes.length" class="panel muted">还没有场景记录</p>
    </div>
    <p v-if="loadingScene" class="muted" role="status">正在读取 {{ loadingScene }}…</p>
    <template v-if="detail?.session">
      <section class="panel detail-panel"><div class="panel-header"><div><h2>{{ detail.session.scene_id }}</h2><p class="muted">{{ participants.length }} 位已记录成员 · 事实版本 {{ detail.session.version }}</p></div><button @click="detail = null">关闭详情</button></div>
        <div class="detail-facts"><span>最近事件：{{ fmtTime(detail.session.last_event_at) }}</span><span>最近实际发言：{{ fmtTime(detail.session.last_bot_message_at) }}</span><span>发言后新增群友消息：{{ detail.session.human_messages_since_bot }}</span></div>
      </section>
      <div class="detail-grid">
        <section class="panel"><h2>参与者</h2><p class="muted">账号昵称和群名片来自消息事件，称呼偏好单独保留在认识中。</p><div class="table-scroll"><table><thead><tr><th>账号</th><th>昵称</th><th>群名片</th><th>群角色</th></tr></thead><tbody><tr v-for="person in participants" :key="person.actor_id"><td><code>{{ person.actor_id }}</code></td><td>{{ person.nickname || '—' }}</td><td>{{ person.card || '—' }}</td><td>{{ person.role || '—' }}</td></tr><tr v-if="!participants.length"><td colspan="4" class="muted">暂无参与者事实</td></tr></tbody></table></div></section>
        <section class="panel"><h2>称呼与互动偏好</h2><article v-for="memory in detail.preferences || []" :key="memory.id" class="record"><div><span class="tag">{{ basisLabel(memory.basis) }}</span><code>{{ memory.subject }}</code></div><p>{{ memory.statement }}</p><details><summary>来源与适用时间</summary><p>{{ memory.evidence?.join('、') || '未附来源' }}</p><p>到期时间：{{ memory.expires_at ? fmtTime(memory.expires_at) : '未设置' }}</p></details></article><p v-if="!detail.preferences?.length" class="muted">暂无有效的称呼与互动偏好</p></section>
        <section class="panel"><h2>信息工作</h2><article v-for="job in detail.jobs || []" :key="job.id" class="record"><div><span class="tag">{{ jobStatus(job.status) }}</span><span class="muted">目标版本 {{ job.revision }}</span></div><p>{{ job.goal }}</p><p v-if="job.result?.summary" class="muted">{{ job.result.summary }}</p><details><summary>工作详情</summary><pre>{{ JSON.stringify(job, null, 2) }}</pre></details></article><p v-if="!detail.jobs?.length" class="muted">暂无信息工作</p></section>
        <section class="panel"><h2>消息是否送达</h2><article v-for="event in detail.recent_deliveries || []" :key="event.id" class="record"><div><span class="tag" :class="event.event_type === 'MESSAGE_SENT' ? 'ok' : 'warn'">{{ deliveryLabel(event) }}</span><time class="muted">{{ fmtTime(event.timestamp) }}</time></div><p class="message-copy">{{ eventText(event) }}</p><p v-if="event.payload?.error" class="bad-text">{{ event.payload.error }}</p><details><summary>回执详情</summary><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></details></article><p v-if="!detail.recent_deliveries?.length" class="muted">暂无发送回执</p></section>
      </div>
      <section class="panel"><h2>近期原话</h2><article v-for="event in detail.recent_messages || []" :key="event.id" class="record"><div><strong>{{ sender(event) }}</strong><time class="muted">{{ fmtTime(event.timestamp) }}</time></div><p class="message-copy">{{ eventText(event) }}</p><details><summary>事件来源</summary><code>{{ event.id }}</code><p>{{ event.actor_id }}</p></details></article><p v-if="!detail.recent_messages?.length" class="muted">暂无近期原话</p></section>
    </template>
  </div>
</template>

<style scoped>
.scene-card { text-align:left;white-space:normal;color:var(--text);min-width:0;cursor:pointer }.scene-card:hover,.scene-card.selected { border-color:var(--border-accent) }.scene-header { display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px }.scene-header h3 { margin:0;overflow-wrap:anywhere }.detail-panel { margin-top:24px }.detail-facts { display:flex;gap:20px;flex-wrap:wrap;color:var(--text-soft);font-size:.86rem }.detail-grid { display:grid;grid-template-columns:1fr 1fr;gap:18px }.detail-grid .panel { min-width:0 }.record { padding:14px 0;border-bottom:1px solid var(--border);overflow-wrap:anywhere }.record:last-child { border-bottom:0 }.record>div { display:flex;gap:10px;align-items:center;flex-wrap:wrap }.record p { margin:9px 0;line-height:1.6 }.record time { font-size:.78rem }.message-copy { white-space:pre-wrap }.table-scroll { overflow-x:auto }details { font-size:.8rem;color:var(--muted) }summary { cursor:pointer }pre { white-space:pre-wrap;overflow-wrap:anywhere }@media(max-width:920px){.detail-grid{grid-template-columns:1fr}}
</style>
