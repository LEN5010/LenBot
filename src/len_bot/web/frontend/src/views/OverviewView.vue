<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const error = ref('')

onMounted(load)
async function load() {
  try {
    data.value = await api('/api/overview/stats')
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="overview-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>运行总览监控看板</h1>
        <p class="muted">全域社交运行时、持续认知与状态量指标</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新状态</span>
      </button>
    </div>

    <p v-if="error" class="tag bad">{{ error }}</p>

    <template v-if="data">
      <!-- Bento Cards Grid -->
      <div class="bento-grid">
        <!-- Bento Card 1: 运行时核心 -->
        <div class="bento-card bento-col-4">
          <div class="bento-badge">⚡ 核心运行时 · RUNTIME</div>
          <div class="bento-hero-stat">
            {{ Math.floor(data.stats.uptime_seconds / 60) }}<span class="unit">分钟</span>
          </div>
          <div class="bento-desc">服务稳定运行中，认知引擎处于就绪态</div>
          <div class="kv-list">
            <div class="kv">
              <span class="k">OneBot 协议端</span>
              <span :class="data.stats.websocket_connected ? 'tag ok' : 'tag bad'">
                {{ data.stats.websocket_connected ? '已连接' : '未连接' }}
              </span>
            </div>
            <div class="kv">
              <span class="k">影子模式 (Shadow)</span>
              <span :class="data.stats.shadow_mode ? 'tag warn' : 'tag ok'">
                {{ data.stats.shadow_mode ? '开启 (推演不实发)' : '关闭 (实时投递)' }}
              </span>
            </div>
            <div class="kv">
              <span class="k">常规模型 (Normal)</span>
              <span class="v code-text">{{ data.stats.normal_model }}</span>
            </div>
            <div class="kv">
              <span class="k">深思模型 (Deliberate)</span>
              <span class="v code-text">{{ data.stats.deliberate_model }}</span>
            </div>
          </div>
        </div>

        <!-- Bento Card 2: 社交认知与表达 -->
        <div class="bento-card bento-col-4">
          <div class="bento-badge">🎯 社交认知与表达</div>
          <div class="bento-hero-stat">
            {{ (data.social_metrics.visible_speech_ratio * 100).toFixed(1) }}<span class="unit">%</span>
          </div>
          <div class="bento-desc">看懂、沉默、提议发言与最终投递</div>
          <div class="kv-list">
            <div class="kv">
              <span class="k">人类消息接收</span>
              <span class="v highlight">{{ data.social_metrics.human_messages }}</span>
            </div>
            <div class="kv">
              <span class="k">认知 / 提议发言</span>
              <span class="v">{{ data.social_metrics.social_cognition }} / {{ data.social_metrics.social_would_speak }}</span>
            </div>
            <div class="kv">
              <span class="k">实际发出可见消息</span>
              <span class="v">{{ data.social_metrics.visible_messages }}</span>
            </div>
            <div class="kv">
              <span class="k">理解后主动静默</span>
              <span class="v ok-text">{{ data.social_metrics.intentional_silence }}</span>
            </div>
            <div class="kv">
              <span class="k">推演拦截 (Would-send)</span>
              <span class="v warn-text">{{ data.social_metrics.would_send }}</span>
            </div>
          </div>
        </div>

        <!-- Bento Card 3: 状态实体量 -->
        <div class="bento-card bento-col-4">
          <div class="bento-badge">📦 持久化状态实体</div>
          <div class="bento-hero-stat">
            {{ data.stats.total_events }}<span class="unit">条</span>
          </div>
          <div class="bento-desc">不可篡改事件日志库总条目数</div>
          <div class="kv-list">
            <div class="kv">
              <span class="k">活跃场景 / 总场景</span>
              <span class="v">{{ data.stats.active_scenes }} / {{ data.stats.total_scenes }}</span>
            </div>
            <div class="kv">
              <span class="k">待执行任务 (Pending Tasks)</span>
              <span class="v highlight">{{ data.stats.pending_tasks }}</span>
            </div>
            <div class="kv">
              <span class="k">未闭环承诺 (Open Loops)</span>
              <span class="v highlight">{{ data.stats.active_open_loops }}</span>
            </div>
            <div class="kv">
              <span class="k">社会记忆信念 (Beliefs)</span>
              <span class="v">{{ data.stats.memory_beliefs_count }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Bento Panel: 实时场景列表 -->
      <div class="panel scene-panel">
        <div class="panel-header">
          <h2>实时会话场景监控 (Scenes)</h2>
          <span class="tag">共 {{ data.scenes.length }} 个跟踪场景</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>场景标识 (Scene ID)</th>
              <th>版本</th>
              <th>场景活跃度</th>
              <th>Social Core 话题</th>
              <th>智能体参与度</th>
              <th>连续发言</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in data.scenes" :key="s.scene_id">
              <td><code>{{ s.scene_id }}</code></td>
              <td><span class="tag">v{{ s.version }}</span></td>
              <td>
                <span class="tag" :class="s.activity === 'HIGH' ? 'ok' : s.activity === 'MEDIUM' ? 'warn' : ''">
                  {{ s.activity }}
                </span>
              </td>
              <td>{{ s.topics?.map(t => t.subject).join(' / ') || '—' }}</td>
              <td>
                <span class="tag" :class="s.engagement === 'participating' ? 'ok' : ''">
                  {{ s.engagement }}
                </span>
              </td>
              <td>{{ s.consecutive_bot_messages }}</td>
            </tr>
            <tr v-if="!data.scenes.length">
              <td colspan="6" class="muted" style="text-align: center; padding: 24px;">暂无会话场景记录</td>
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

.bento-col-4 {
  grid-column: span 4;
}

@media (max-width: 1080px) {
  .bento-col-4 {
    grid-column: span 12;
  }
}

.unit {
  font-size: 1.05rem;
  font-weight: 500;
  color: var(--muted);
  margin-left: 4px;
}

.kv-list {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
}

.highlight {
  color: #60a5fa;
  font-weight: 600;
}

.code-text {
  font-family: monospace;
  font-size: 0.84rem;
}

.ok-text {
  color: #34d399;
}

.warn-text {
  color: #fbbf24;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.panel-header h2 {
  margin: 0;
}
</style>
