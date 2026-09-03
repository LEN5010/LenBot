<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const traces = ref([])
const selected = ref(null)
const filters = ref({ scene_id: '', kind: '' })
const error = ref('')

onMounted(load)
async function load() {
  try {
    const params = new URLSearchParams()
    if (filters.value.scene_id) params.set('scene_id', filters.value.scene_id)
    if (filters.value.kind) params.set('kind', filters.value.kind)
    params.set('limit', '100')
    traces.value = await api('/api/cockpit/traces?' + params.toString())
  } catch (e) {
    error.value = e.message
  }
}

const selectedDetail = ref(null)
function openTrace(t) {
  selected.value = t
  selectedDetail.value = JSON.stringify(t.payload, null, 2)
}
</script>

<template>
  <div class="trace-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>决策因果链路 (Trace)</h1>
        <p class="muted">刺激源 → 注意力评估 → 认知推理 → 运行门禁 → 持久化影响的完整因果链条</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新链路</span>
      </button>
    </div>

    <div class="toolbar filter-bar">
      <input v-model="filters.scene_id" placeholder="输入 scene_id 精确过滤..." />
      <select v-model="filters.kind">
        <option value="">全部链路类型</option>
        <option value="attention">注意力评估 (Attention)</option>
        <option value="episode">认知推演周期 (Episode)</option>
      </select>
      <button @click="load">筛选</button>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>发生时间</th>
            <th>链路类型</th>
            <th>关联场景</th>
            <th>参考标识 (Ref ID)</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in traces" :key="t.id" :class="{ 'active-row': selected?.id === t.id }">
            <td>{{ fmtTime(t.created_at) }}</td>
            <td>
              <span class="tag" :class="t.kind === 'episode' ? 'warn' : 'ok'">{{ t.kind }}</span>
            </td>
            <td><code>{{ t.scene_id }}</code></td>
            <td class="muted"><code>{{ t.ref_id }}</code></td>
            <td>
              <button class="small-btn" @click="openTrace(t)">查看因果</button>
            </td>
          </tr>
          <tr v-if="!traces.length">
            <td colspan="5" class="muted" style="text-align: center; padding: 24px;">暂无匹配的决策因果链记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Selected Trace Details Bento Panel -->
    <div v-if="selected" class="panel detail-panel">
      <div class="trace-header">
        <h3>因果链快照 · <code>{{ selected.id }}</code> ({{ selected.kind }})</h3>
        <span class="tag" :class="selected.kind === 'episode' ? 'warn' : 'ok'">{{ selected.kind }}</span>
      </div>

      <template v-if="selected.kind === 'episode' && selected.payload">
        <div class="bento-grid" style="margin-top: 14px;">
          <div class="bento-card bento-col-6">
            <div class="bento-badge">📡 刺激输入与注意力评估</div>
            <div class="kv">
              <span class="k">刺激源 (Stimulus)</span>
              <span class="v highlight">{{ selected.payload.stimulus?.actor_id }}: {{ selected.payload.stimulus?.text }}</span>
            </div>
            <div class="kv">
              <span class="k">注意力判定 (Attention)</span>
              <span class="tag" :class="selected.payload.attention?.disposition === 'WAKE' ? 'ok' : 'warn'">
                {{ selected.payload.attention?.disposition }}
              </span>
            </div>
            <div class="kv">
              <span class="k">注意力决策原因</span>
              <span class="v">{{ selected.payload.attention?.reason }}</span>
            </div>
          </div>

          <div class="bento-card bento-col-6">
            <div class="bento-badge">🧠 认知推理与安全门禁</div>
            <div class="kv">
              <span class="k">推理模式与步数</span>
              <span class="v">{{ selected.payload.cognition?.mode }} · {{ selected.payload.cognition?.steps?.length }} 步推理</span>
            </div>
            <div class="kv">
              <span class="k">认知结论 (Outcome)</span>
              <span class="v ok-text">{{ selected.payload.outcome?.disposition }}</span>
            </div>
            <div class="kv">
              <span class="k">决策理由 (Decision Reason)</span>
              <span class="v">{{ selected.payload.outcome?.decision_reason || selected.payload.outcome?.thought || '—' }}</span>
            </div>
            <div class="kv">
              <span class="k">运行时门禁 (Gate)</span>
              <span class="tag" :class="selected.payload.gate?.disposition === 'ACCEPT' ? 'ok' : 'bad'">
                {{ selected.payload.gate?.disposition }} ({{ selected.payload.gate?.reason || '无' }})
              </span>
            </div>
          </div>
        </div>
      </template>

      <h4 style="margin: 16px 0 8px; color: var(--muted)">原始状态载荷 JSON</h4>
      <pre>{{ selectedDetail }}</pre>
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

.small-btn {
  padding: 4px 10px;
  font-size: 0.82rem;
}

.active-row td {
  background: rgba(99, 102, 241, 0.08);
}

.trace-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  padding-bottom: 12px;
}
.trace-header h3 {
  margin: 0;
}

.bento-col-6 {
  grid-column: span 6;
}

@media (max-width: 900px) {
  .bento-col-6 {
    grid-column: span 12;
  }
}

.highlight {
  color: #60a5fa;
  font-weight: 500;
}
.ok-text {
  color: #34d399;
  font-weight: 600;
}
</style>
