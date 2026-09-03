<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const scenes = ref([])
const detail = ref(null)
const error = ref('')

onMounted(load)
async function load() {
  try { scenes.value = (await api('/api/cockpit/scenes')).scenes } catch (e) { error.value = e.message }
}

async function openDetail(sceneId) {
  error.value = ''
  try {
    detail.value = await api(`/api/cockpit/scenes/${encodeURIComponent(sceneId)}`)
    detail.value.timeline = await api(`/api/cockpit/traces?scene_id=${encodeURIComponent(sceneId)}&limit=20`)
  } catch (e) { error.value = e.message }
}

async function injectEvent() {
  if (!detail.value) return
  const text = prompt('注入事件内容:')
  if (!text) return
  try {
    await api(`/api/cockpit/scenes/${encodeURIComponent(detail.value.scene_id)}/inject`, {
      method: 'POST', body: JSON.stringify({ raw_text: text, actor_id: 'user:admin' }),
    })
    await openDetail(detail.value.scene_id)
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <h1>Scenes</h1>
    <p v-if="error" class="tag bad">{{ error }}</p>
    <div class="grid cards">
      <div v-for="s in scenes" :key="s.scene_id" class="card clickable" @click="openDetail(s.scene_id)">
        <h3>{{ s.scene_id }} <span v-if="!s.is_in_memory" class="tag">未加载</span></h3>
        <div class="kv"><span class="k">Activity</span><span>{{ s.activity_level }}</span></div>
        <div class="kv"><span class="k">Topic</span><span>{{ s.active_topic || '—' }}</span></div>
        <div class="kv"><span class="k">Thread</span>
          <span v-if="s.current_thread">{{ s.current_thread.topic }} ({{ s.current_thread.status }})</span>
          <span v-else class="muted">无</span></div>
        <div class="kv"><span class="k">Participants</span><span>{{ s.participant_count }}</span></div>
      </div>
    </div>

    <template v-if="detail">
      <h2>Scene Detail · {{ detail.scene_id }}</h2>
      <div class="panel">
        <div class="kv"><span class="k">Version</span><span>{{ detail.version }}</span></div>
        <div class="kv"><span class="k">Activity</span><span>{{ detail.activity_level }}</span></div>
        <div class="kv"><span class="k">Bot Engagement</span><span>{{ detail.bot_engagement }}</span></div>
        <div class="kv"><span class="k">Active Topic</span><span>{{ detail.active_topic || '—' }}</span></div>
        <div class="kv"><span class="k">Participants</span><span>{{ detail.participants.join(', ') || '—' }}</span></div>
        <div class="kv" v-if="detail.current_thread"><span class="k">Participation Thread</span>
          <span>{{ detail.current_thread.topic }} · {{ detail.current_thread.status }} · 干预 {{ detail.current_thread.intervening_messages }} 条</span></div>
        <button style="margin-top:10px" @click="injectEvent">注入事件</button>
      </div>

      <h2>最近行为链 (Trace)</h2>
      <div class="panel">
        <table>
          <thead><tr><th>时间</th><th>Kind</th><th>Ref</th><th>概要</th></tr></thead>
          <tbody>
            <tr v-for="t in detail.timeline" :key="t.id">
              <td>{{ new Date(t.created_at * 1000).toLocaleTimeString() }}</td>
              <td><span class="tag" :class="t.kind === 'episode' ? 'warn' : 'ok'">{{ t.kind }}</span></td>
              <td class="muted">{{ t.ref_id.slice(0, 16) }}</td>
              <td>
                <template v-if="t.kind === 'attention'">{{ t.payload.disposition }} · {{ t.payload.reason }} · {{ t.payload.text }}</template>
                <template v-else>{{ t.payload.outcome?.disposition }} · gate={{ t.payload.gate?.disposition }} · 动作 {{ t.payload.actions_enqueued }}</template>
              </td>
            </tr>
            <tr v-if="!detail.timeline?.length"><td colspan="4" class="muted">暂无 trace</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.card.clickable { cursor: pointer; }
.card.clickable:hover { border-color: var(--accent); }
</style>
