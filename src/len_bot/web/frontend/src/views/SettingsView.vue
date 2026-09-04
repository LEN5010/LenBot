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
      ? '试运行已开启，机器人不会真实发送消息'
      : '试运行已关闭，机器人将开始真实发送消息'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function annotate(item, label) {
  const comment = prompt(`可以补充一句评价：`)
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
    message.value = '评价已保存'
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
        <h1>系统设置</h1>
        <p class="muted">控制是否真实发送消息，并管理登录密码。</p>
      </div>
      <button class="primary" @click="load">
        <span>刷新</span>
      </button>
    </div>

    <p v-if="message" class="tag ok" style="margin-bottom: 16px;">✓ {{ message }}</p>
    <p v-if="error" class="tag bad" style="margin-bottom: 16px;">✕ {{ error }}</p>

    <div class="panel">
      <div class="panel-header">
        <div>
          <h2>试运行模式</h2>
          <p class="muted">开启后机器人照常观察和思考，但不会向 QQ 发送任何消息。</p>
        </div>
        <button :class="shadow.enabled ? 'danger' : 'primary'" @click="toggleShadow">
          {{ shadow.enabled ? '关闭试运行，开始真实发送' : '开启试运行，不真实发送' }}
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
          <div class="bento-badge">整体判断正确率</div>
          <div class="bento-hero-stat">{{ (annotations.accuracy * 100).toFixed(1) }}<span class="unit">%</span></div>
          <div class="bento-desc">说话和沉默判断正确的比例</div>
        </div>
        <div class="bento-card bento-col-3">
          <div class="bento-badge">发言合适率</div>
          <div class="bento-hero-stat">{{ (annotations.precision * 100).toFixed(1) }}<span class="unit">%</span></div>
          <div class="bento-desc">已经发言的内容中，适合开口的比例</div>
        </div>
        <div class="bento-card bento-col-3">
          <div class="bento-badge">人工评价分布</div>
          <div class="kv" style="padding: 2px 0;"><span class="k">合适发言</span><span class="v ok-text">{{ annotations.stats.TP }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">不当插嘴</span><span class="v bad-text">{{ annotations.stats.FP }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">正确沉默</span><span class="v">{{ annotations.stats.TN }}</span></div>
          <div class="kv" style="padding: 2px 0;"><span class="k">错过参与</span><span class="v warn-text">{{ annotations.stats.FN }}</span></div>
        </div>
      </div>

      <!-- Would-send Log Table -->
      <h3 style="margin: 18px 0 10px;">机器人原本想发送的消息</h3>
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
                <button class="small-btn ok-btn" @click="annotate(w, 'TP')">这句合适</button>
                <button class="small-btn bad-btn" @click="annotate(w, 'FP')">不该插嘴</button>
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
        <h2>登录安全</h2>
        <span class="tag" :class="me.is_default_password ? 'bad' : 'ok'">
          {{ me.is_default_password ? '⚠️ 初始密码未修改' : '✓ 密码处于安全状态' }}
        </span>
      </div>

      <div class="kv"><span class="k">当前管理员</span><span class="highlight">{{ me.username }}</span></div>
      <div class="kv"><span class="k">上次登录时间</span><span>{{ me.last_login_at ? fmtTime(me.last_login_at) : '—' }}</span></div>

      <div class="password-change-box" style="margin-top: 14px;">
        <h4 style="margin: 0 0 10px; color: var(--text-soft);">修改访问密码</h4>
        <div class="toolbar">
          <input v-model="form.current_password" type="password" placeholder="原密码" />
          <input v-model="form.new_password" type="password" placeholder="新密码，至少 6 位" />
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
  color: var(--text-soft);
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
  color: var(--text);
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
  color: var(--ok);
}
.ok-btn:hover {
  background: rgba(16, 185, 129, 0.25);
}

.bad-btn {
  background: var(--bad-bg);
  border-color: rgba(239, 68, 68, 0.3);
  color: var(--bad);
}
.bad-btn:hover {
  background: rgba(239, 68, 68, 0.25);
}

.highlight {
  color: var(--accent-strong);
  font-weight: 500;
}
.ok-text {
  color: var(--ok);
}
.warn-text {
  color: var(--warn);
}
.bad-text {
  color: var(--bad);
}

.password-change-box {
  background: rgba(239, 246, 255, 0.66);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
}
</style>
