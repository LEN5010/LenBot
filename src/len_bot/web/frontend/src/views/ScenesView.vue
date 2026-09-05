<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const scenes = ref([])
const detail = ref(null)
const error = ref('')

onMounted(load)
async function load() {
  try {
    scenes.value = (await api('/api/cockpit/scenes')).scenes
  } catch (e) {
    error.value = e.message
  }
}

async function openDetail(sceneId) {
  error.value = ''
  try {
    detail.value = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
    detail.value.timeline = await api(`/api/cockpit/traces?scene_id=${encodeURIComponent(sceneId)}&limit=20`)
  } catch (e) {
    error.value = e.message
  }
}

function activityLabel(value) {
  return value === 'HIGH' ? '很活跃' : value === 'MEDIUM' ? '有消息' : '安静'
}

function engagementLabel(value) {
  return value === 'participating' || value === 'active' ? '正在参与' : value === 'lightly_participating' ? '偶尔参与' : '正在旁观'
}

function traceLabel(value) {
  return value === 'social_cognition_error' ? '处理失败' : '社交判断'
}
function deliveryLabel(event) {
  if (event.event_type === 'ACTION_SHADOWED') return '仅观察，未发送'
  return {sent: '已送达', not_sent: '未发出', rejected: '接口拒绝', unknown: '结果不确定'}[event.payload.delivery_status]
    || (event.event_type === 'MESSAGE_SENT' ? '已送达' : '旧记录缺少详细原因')
}
</script>

<template>
  <div class="scenes-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>群聊状态</h1>
        <p class="muted">查看机器人正在关注哪些群、聊什么，以及它当前是否参与。</p>
      </div>
      <button class="primary" @click="load">
        <span>刷新</span>
      </button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <!-- Bento Scene Cards Grid -->
    <div class="grid cards">
      <div
        v-for="s in scenes"
        :key="s.scene_id"
        class="card clickable bento-scene-card"
        :class="{ selected: detail?.scene_id === s.scene_id }"
        @click="openDetail(s.scene_id)"
      >
        <div class="scene-header">
          <h3><code>{{ s.scene_id }}</code></h3>
          <span v-if="!s.is_in_memory" class="tag">未装载</span>
          <span v-else class="tag ok">活跃中</span>
        </div>
        <div class="kv">
          <span class="k">最近状态</span>
          <span class="tag" :class="s.activity_level === 'HIGH' ? 'ok' : s.activity_level === 'MEDIUM' ? 'warn' : ''">
            {{ activityLabel(s.activity_level) }}
          </span>
        </div>
        <div class="kv">
          <span class="k">当前话题</span>
          <span class="v">{{ s.social_world?.topics?.map(t => t.subject).join(' / ') || '—' }}</span>
        </div>
        <div class="kv">
          <span class="k">机器人状态</span>
          <span class="v highlight">{{ engagementLabel(s.self_social_state?.engagement) }}</span>
        </div>
        <div class="kv">
          <span class="k">参与成员数</span>
          <span class="v">{{ s.participant_count }} 人</span>
        </div>
      </div>
    </div>

    <!-- Scene Detail Panel -->
    <template v-if="detail">
      <div class="panel detail-panel">
        <div class="detail-header">
          <div>
            <h2>场景详情 · <code>{{ detail.scene_id }}</code></h2>
            <p class="muted">机器人{{ engagementLabel(detail.self_social_state?.engagement) }}，最近有 {{ detail.participants?.length || 0 }} 位成员参与</p>
          </div>
        </div>

        <div class="detail-grid">
          <div class="kv">
            <span class="k">最近状态</span>
            <span class="tag" :class="detail.activity_level === 'HIGH' ? 'ok' : 'warn'">
              {{ activityLabel(detail.activity_level) }}
            </span>
          </div>
          <div class="kv">
            <span class="k">正在聊的话题</span>
            <span class="v highlight">{{ detail.social_world?.topics?.map(t => t.subject).join(' / ') || '—' }}</span>
          </div>
          <div class="kv">
            <span class="k">最近参与成员</span>
            <span class="v">{{ detail.participants.join(', ') || '—' }}</span>
          </div>
          <div class="kv">
            <span class="k">还没聊完的事</span>
            <span class="v">{{ detail.social_world?.open_threads?.filter(t => t.status !== 'resolved').map(t => t.unresolved || t.summary).join(' / ') || '—' }}</span>
          </div>
        </div>
      </div>

      <div class="grid cards">
        <div class="panel">
          <h2>现在怎样称呼大家</h2>
          <div v-for="p in detail.working_persons" :key="p.actor_id" class="kv">
            <div><strong>{{ p.preferred_name || p.card || p.nickname || p.display_name || '尚未确认称呼' }}</strong>
              <p class="muted">昵称：{{ p.nickname || '未知' }} · 群名片：{{ p.card || '未设置' }}</p>
              <p>{{ p.recent_context.join('；') }}</p>
              <details><summary>来源与账号</summary><code>{{ p.actor_id }}</code><p>{{ p.recent_event_ids.join('、') }}</p></details>
            </div>
          </div>
        </div>
        <div class="panel">
          <h2>最近收到的反馈</h2>
          <p v-for="feedback in detail.self_social_state?.recent_feedback" :key="feedback">{{ feedback }}</p>
          <p v-if="!detail.self_social_state?.recent_feedback?.length" class="muted">暂未记录明确反馈</p>
          <h3>相处方式</h3>
          <p v-for="r in detail.working_relationships" :key="r.actor_id">{{ detail.working_persons[r.actor_id]?.preferred_name || detail.working_persons[r.actor_id]?.display_name || r.actor_id }}：{{ r.patterns.join('；') }}</p>
        </div>
        <div class="panel">
          <h2>最近修订的认识</h2>
          <div v-for="m in detail.recent_memory_changes" :key="m.id" class="kv">
            <div><span class="tag">{{ m.operation === 'refute' ? '已撤销' : m.operation === 'supersede' ? '已替代旧认识' : '已记住' }}</span>
              <p>{{ m.value }}</p><p class="muted">{{ m.reason }}</p>
              <details><summary>证据详情</summary><code>{{ m.id }}</code><p>{{ m.evidence.join('、') }}</p></details>
            </div>
          </div>
          <p v-if="!detail.recent_memory_changes?.length" class="muted">暂无修订记录</p>
        </div>
        <div class="panel">
          <h2>消息是否送达</h2>
          <div v-for="e in detail.recent_deliveries" :key="e.id" class="kv">
            <div><span class="tag" :class="e.event_type === 'MESSAGE_SEND_FAILED' ? 'bad' : 'ok'">{{ deliveryLabel(e) }}</span>
              <p>{{ e.payload.content }}</p><p class="muted">{{ e.payload.error }}</p>
              <details><summary>发送详情</summary><p>{{ new Date(e.timestamp * 1000).toLocaleString() }}</p><code>{{ e.payload.action_id }}</code></details>
            </div>
          </div>
          <p v-if="!detail.recent_deliveries?.length" class="muted">暂无发送记录</p>
        </div>
      </div>

      <h2>最近的机器人动态</h2>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>类型</th>
              <th>机器人理解</th>
              <th>最终决定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in detail.timeline" :key="t.id">
              <td>{{ new Date(t.created_at * 1000).toLocaleTimeString() }}</td>
              <td>
                <span class="tag" :class="t.kind === 'social_cognition_error' ? 'bad' : 'ok'">{{ traceLabel(t.kind) }}</span>
              </td>
              <td>{{ t.payload.result?.perception?.summary || t.payload.error || '—' }}</td>
              <td>{{ t.payload.result?.decision?.action === 'speak' ? '准备发言' : t.payload.result?.decision?.action === 'silence' ? '选择沉默' : '未完成' }}<p class="muted">{{ t.payload.result?.decision?.reason || t.payload.gate?.reason }}</p></td>
            </tr>
            <tr v-if="!detail.timeline?.length">
              <td colspan="4" class="muted" style="text-align: center; padding: 20px;">该场景暂无行为链路记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
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

.bento-scene-card {
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
.bento-scene-card:hover {
  border-color: var(--border-accent);
  transform: translateY(-2px);
}
.bento-scene-card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent) inset, var(--shadow-md);
  background: rgba(235, 243, 255, 0.92);
}

.scene-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.scene-header h3 {
  margin: 0;
  font-size: 0.95rem;
}

.detail-panel {
  margin-top: 24px;
}
.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.detail-header h2 {
  margin: 0;
}
.detail-header p {
  margin: 4px 0 0;
  font-size: 0.84rem;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 12px;
}

.highlight { color: var(--accent-strong); font-weight: 600; }
</style>
