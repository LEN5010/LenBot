<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const me = ref(null)
const shadow = ref({ enabled: false, would_send: [] })
const annotations = ref({ annotations: [], stats: { TP: 0, FP: 0, TN: 0, FN: 0 }, total: 0, accuracy: 0, precision: 0 })
const form = ref({ current_password: '', new_password: '' })
const message = ref('')
const error = ref('')

onMounted(load)
async function load() {
  try {
    me.value = await api('/api/auth/me')
    const shadowRes = await api('/api/cockpit/shadow')
    shadow.value.enabled = !!shadowRes.enabled
    shadow.value.would_send = shadowRes.would_send || []

    const annRes = await api('/api/cockpit/shadow-annotations')
    annotations.value = annRes
  } catch (e) {
    error.value = e.message
  }
}

async function toggleShadow() {
  error.value = ''
  message.value = ''
  try {
    const res = await api('/api/cockpit/shadow/toggle', {
      method: 'POST',
      body: JSON.stringify({ enabled: !shadow.value.enabled }),
    })
    shadow.value.enabled = res.shadow_mode
    message.value = res.shadow_mode
      ? '影子模式已开启：智能体正常推演，但拦截一切真实消息发送，记录推演意图'
      : '影子模式已关闭：恢复网络真实消息投递'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function annotate(item, label) {
  const comment = prompt(`为该条推演添加评测备注 (判定为 ${label})：`)
  if (comment === null) return
  try {
    await api('/api/cockpit/shadow-annotations', {
      method: 'POST',
      body: JSON.stringify({
        scene_id: item.scene_id,
        stimulus_id: item.stimulus_id,
        label,
        comment
      })
    })
    message.value = `评测打标 [${label}] 已保存`
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function changePassword() {
  error.value = ''
  message.value = ''
  try {
    await api('/api/auth/change_password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: form.value.current_password,
        new_password: form.value.new_password
      }),
    })
    message.value = '密码已成功修改'
    form.value = { current_password: '', new_password: '' }
    await load()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="settings-view">
    <div class="toolbar">
      <div class="page-title">
        <h1>系统设置与策略调优 (Settings & Security)</h1>
        <p class="muted">Social Core 影子推演打标评测与管理员安全凭据管控</p>
      </div>
      <button class="primary" @click="load">
        <span>⟳ 刷新配置</span>
      </button>
    </div>

    <p v-if="message" class="tag ok" style="margin-bottom: 16px;">✓ {{ message }}</p>
    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <div class="panel">
      <div class="panel-header">
        <div>
          <h2>影子推演模式与人工评测 (Shadow Mode)</h2>
          <p class="muted">开启后运行时照常感知、推理与提案，但拦截物理投递，用于评估话痨率与发言质量</p>
        </div>
        <button :class="shadow.enabled ? 'danger' : 'primary'" @click="toggleShadow">
          {{ shadow.enabled ? '✕ 关闭影子模式 (恢复实发)' : '▶ 开启影子模式 (纯推演)' }}
        </button>
      </div>

      <!-- Bento Stats: Shadow Accuracy & Precision -->
      <div class="bento-grid" style="margin: 16px 0;">
        <div class="bento-card bento-col-3">
          <div class="bento-badge">🎯 评测总样本数</div>
          <div class="bento-hero-stat">{{ annotations.total }}<span class="unit">条</span></div>
          <div class="bento-desc">人工已复核标注决策数</div>
        </div>
        <div class="bento-card bento-col-3">
          <div class="bento-badge">📊 发言准确率 (Accuracy)</div>
          <div class="bento-hero-stat">{{ (annotations.accuracy * 100).toFixed(1) }}<span class="unit">%</span></div>
          <div class="bento-desc">(TP + TN) / 总数</div>
        </div>
        <div class="bento-card bento-col-3">
          <div class="bento-badge">✨ 查准率 (Precision)</div>
          <div class="bento-hero-stat">{{ (annotations.precision * 100).toFixed(1) }}<span class="unit">%</span></div>
          <div class="bento-desc">TP / (TP + FP)</div>
        </div>
        <div class="bento-card bento-col-3">
          <div class="bento-badge">🏷️ 混淆矩阵分布</div>
          <div class="kv" style="padding: 2px 0;"><span class="k">真正 (TP)</span><span class="v ok-text">{{ annotations.stats.TP }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">假正 (FP)</span><span class="v bad-text">{{ annotations.stats.FP }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">真负 (TN)</span><span class="v">{{ annotations.stats.TN }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">假负 (FN)</span><span class="v warn-text">{{ annotations.stats.FN }}</span></div>
        </div>
      </div>

      <!-- Would-send Log Table -->
      <h3 style="margin: 18px 0 10px;">拟发送推演记录 (Would-Send Log)</h3>
      <table>
        <thead>
          <tr>
            <th>推演时间</th>
            <th>会话场景</th>
            <th>拟发送消息内容</th>
            <th>预期回复</th>
            <th>人工打标评测</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(w, i) in shadow.would_send" :key="i">
            <td>{{ fmtTime(w.timestamp) }}</td>
            <td><code>{{ w.scene_id }}</code></td>
            <td class="content-cell">{{ w.content }}</td>
            <td><code>{{ w.reply_target || '—' }}</code></td>
            <td>
              <div class="action-btn-group">
                <button class="small-btn ok-btn" @click="annotate(w, 'TP')">TP 正确发言</button>
                <button class="small-btn bad-btn" @click="annotate(w, 'FP')">FP 不当插嘴</button>
              </div>
            </td>
          </tr>
          <tr v-if="!shadow.would_send.length">
            <td colspan="5" class="muted" style="text-align: center; padding: 20px;">暂无拟发送记录</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="me" style="margin-top: 24px;">
      <div class="panel-header">
        <h2>系统访问安全凭据 (Security)</h2>
        <span class="tag" :class="me.is_default_password ? 'bad' : 'ok'">
          {{ me.is_default_password ? '⚠️ 初始密码未修改' : '✓ 密码处于安全状态' }}
        </span>
      </div>

      <div class="kv"><span class="k">当前管理员</span><span class="highlight">{{ me.username }}</span></div>
      <div class="kv"><span class="k">上次登录时间</span><span>{{ me.last_login_at ? fmtTime(me.last_login_at) : '—' }}</span></div>

      <div class="password-change-box" style="margin-top: 14px;">
        <h4 style="margin: 0 0 10px; color: #cbd5e1;">修改访问密码</h4>
        <div class="toolbar">
          <input v-model="form.current_password" type="password" placeholder="原密码" />
          <input v-model="form.new_password" type="password" placeholder="新密码 (至少6位字符)" />
          <button class="primary" @click="changePassword">更新密码</button>
        </div>
      </div>
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

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.panel-header h2 {
  margin: 0;
}
.panel-header p {
  margin: 4px 0 0;
  font-size: 0.84rem;
}

.form-vertical {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.form-vertical label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.88rem;
  color: #cbd5e1;
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.bento-col-3 {
  grid-column: span 3;
}

@media (max-width: 1080px) {
  .bento-col-3 {
    grid-column: span 6;
  }
}
@media (max-width: 600px) {
  .bento-col-3 {
    grid-column: span 12;
  }
}

.unit {
  font-size: 1.05rem;
  font-weight: 500;
  color: var(--muted);
  margin-left: 4px;
}

.content-cell {
  color: #e2e8f0;
  font-weight: 500;
  max-width: 320px;
}

.action-btn-group {
  display: flex;
  gap: 6px;
}

.small-btn {
  padding: 3px 8px;
  font-size: 0.78rem;
}

.ok-btn {
  background: var(--ok-bg);
  border-color: rgba(16, 185, 129, 0.3);
  color: #34d399;
}
.ok-btn:hover {
  background: rgba(16, 185, 129, 0.25);
}

.bad-btn {
  background: var(--bad-bg);
  border-color: rgba(239, 68, 68, 0.3);
  color: #f87171;
}
.bad-btn:hover {
  background: rgba(239, 68, 68, 0.25);
}

.highlight {
  color: #60a5fa;
  font-weight: 500;
}
.ok-text {
  color: #34d399;
}
.warn-text {
  color: #fbbf24;
}
.bad-text {
  color: #f87171;
}

.password-change-box {
  background: rgba(14, 20, 32, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: var(--radius-md);
  padding: 14px 16px;
}
</style>
