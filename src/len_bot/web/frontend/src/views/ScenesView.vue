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

async function injectEvent() {
  if (!detail.value) return
  const text = prompt('请输入注入事件的模拟消息内容:')
  if (!text) return
  try {
    await api(`/api/cockpit/scenes/${encodeURIComponent(detail.value.scene_id)}/inject`, {
      method: 'POST',
      body: JSON.stringify({ raw_text: text, actor_id: 'user:admin' }),
    })
    await openDetail(detail.value.scene_id)
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="scenes-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>会话场景看板 (Scenes)</h1>
        <p class="muted">单写者场景状态机 (SceneActor)、会话聚焦线程与实时上下文注入</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新场景</span>
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
          <span class="k">场景活跃度</span>
          <span class="tag" :class="s.activity_level === 'HIGH' ? 'ok' : s.activity_level === 'MEDIUM' ? 'warn' : ''">
            {{ s.activity_level }}
          </span>
        </div>
        <div class="kv">
          <span class="k">当前话题</span>
          <span class="v">{{ s.social_world?.topics?.map(t => t.subject).join(' / ') || '—' }}</span>
        </div>
        <div class="kv">
          <span class="k">自身状态</span>
          <span class="v highlight">{{ s.self_social_state?.engagement || 'observing' }}</span>
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
            <p class="muted">当前状态机版本 v{{ detail.version }} · 参与度 {{ detail.self_social_state?.engagement || 'observing' }}</p>
          </div>
          <button class="primary" @click="injectEvent">
            <span>⚡ 模拟注入消息</span>
          </button>
        </div>

        <div class="detail-grid">
          <div class="kv">
            <span class="k">活跃度等级</span>
            <span class="tag" :class="detail.activity_level === 'HIGH' ? 'ok' : 'warn'">
              {{ detail.activity_level }}
            </span>
          </div>
          <div class="kv">
            <span class="k">当前聚焦话题</span>
            <span class="v highlight">{{ detail.social_world?.topics?.map(t => t.subject).join(' / ') || '—' }}</span>
          </div>
          <div class="kv">
            <span class="k">常驻参与成员</span>
            <span class="v">{{ detail.participants.join(', ') || '—' }}</span>
          </div>
          <div class="kv">
            <span class="k">开放社会线程</span>
            <span class="v">{{ detail.social_world?.open_threads?.map(t => t.summary).join(' / ') || '—' }}</span>
          </div>
        </div>
      </div>

      <h2>最近决策链路 (Trace Timeline)</h2>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>追踪类型 (Kind)</th>
              <th>关联标识</th>
              <th>决策推演内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in detail.timeline" :key="t.id">
              <td>{{ new Date(t.created_at * 1000).toLocaleTimeString() }}</td>
              <td>
                <span class="tag" :class="t.kind === 'episode' ? 'warn' : 'ok'">{{ t.kind }}</span>
              </td>
              <td class="muted"><code>{{ t.ref_id.slice(0, 16) }}</code></td>
              <td>
                <template v-if="t.kind === 'attention'">
                  <span class="tag">{{ t.payload.disposition }}</span>
                  <span class="muted">【{{ t.payload.reason }}】</span>
                  {{ t.payload.text }}
                </template>
                <template v-else>
                  <span class="tag warn">{{ t.payload.outcome?.disposition }}</span>
                  门禁: <code>{{ t.payload.gate?.disposition }}</code> · 触发动作: {{ t.payload.actions_enqueued }}
                </template>
              </td>
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
  background: rgba(24, 34, 54, 0.85);
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

.highlight {
  color: #60a5fa;
  font-weight: 500;
}
</style>
