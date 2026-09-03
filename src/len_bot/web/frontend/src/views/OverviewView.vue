<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtAgo } from '../api.js'

const data = ref(null)
const error = ref('')

onMounted(load)
async function load() {
  try { data.value = await api('/api/overview/stats') } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <div class="toolbar">
      <h1 style="margin:0; flex:1">Overview</h1>
      <button @click="load">刷新</button>
    </div>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <template v-if="data">
      <div class="grid cards">
        <div class="card">
          <h3>Runtime</h3>
          <div class="kv"><span class="k">Uptime</span><span>{{ Math.floor(data.stats.uptime_seconds / 60) }} 分钟</span></div>
          <div class="kv"><span class="k">OneBot</span>
            <span :class="data.stats.websocket_connected ? 'tag ok' : 'tag bad'">
              {{ data.stats.websocket_connected ? '已连接' : '未连接' }}
            </span></div>
          <div class="kv"><span class="k">Shadow Mode</span>
            <span :class="data.stats.shadow_mode ? 'tag warn' : 'tag'">
              {{ data.stats.shadow_mode ? '开启(不实发)' : '关闭' }}
            </span></div>
          <div class="kv"><span class="k">Normal / Deliberate</span>
            <span>{{ data.stats.normal_model }} / {{ data.stats.deliberate_model }}</span></div>
        </div>
        <div class="card">
          <h3>状态量</h3>
          <div class="kv"><span class="k">事件总数</span><span>{{ data.stats.total_events }}</span></div>
          <div class="kv"><span class="k">活跃场景</span><span>{{ data.stats.active_scenes }} / {{ data.stats.total_scenes }}</span></div>
          <div class="kv"><span class="k">Pending Tasks</span><span>{{ data.stats.pending_tasks }}</span></div>
          <div class="kv"><span class="k">Active OpenLoops</span><span>{{ data.stats.active_open_loops }}</span></div>
          <div class="kv"><span class="k">活跃记忆</span><span>{{ data.stats.memory_beliefs_count }}</span></div>
        </div>
        <div class="card">
          <h3>Social Behavior (§三十二)</h3>
          <div class="kv"><span class="k">Human messages</span><span>{{ data.social_metrics.human_messages }}</span></div>
          <div class="kv"><span class="k">Observe / Track / Wake</span>
            <span>{{ data.social_metrics.observe }} / {{ data.social_metrics.track }} / {{ data.social_metrics.wake }}</span></div>
          <div class="kv"><span class="k">Visible messages</span><span>{{ data.social_metrics.visible_messages }}</span></div>
          <div class="kv"><span class="k">Visible Speech Ratio</span>
            <span class="tag" :class="data.social_metrics.visible_speech_ratio <= 0.1 ? 'ok' : 'warn'">
              {{ (data.social_metrics.visible_speech_ratio * 100).toFixed(1) }}%
            </span></div>
          <div class="kv"><span class="k">Wake→Silence</span><span>{{ data.social_metrics.wake_silence }}</span></div>
          <div class="kv"><span class="k">Would-send (shadow)</span><span>{{ data.social_metrics.would_send }}</span></div>
        </div>
      </div>

      <h2>Scenes</h2>
      <div class="panel">
        <table>
          <thead><tr><th>Scene</th><th>Version</th><th>Activity</th><th>Topic</th><th>Bot</th><th>Consecutive</th></tr></thead>
          <tbody>
            <tr v-for="s in data.scenes" :key="s.scene_id">
              <td>{{ s.scene_id }}</td><td>{{ s.version }}</td><td>{{ s.activity }}</td>
              <td>{{ s.active_topic }}</td><td>{{ s.bot_engagement }}</td><td>{{ s.consecutive_bot_messages }}</td>
            </tr>
            <tr v-if="!data.scenes.length"><td colspan="6" class="muted">暂无场景</td></tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
