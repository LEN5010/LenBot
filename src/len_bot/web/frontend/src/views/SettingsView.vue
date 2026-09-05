<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const me = ref(null)
const onebot = ref(null)
const exemplars = ref([])
const editingExample = ref(null)
const presetPreview = ref(null)
const personaLabels = {identity_name: '机器人名字', identity_persona: '身份背景', identity_core: '性格与相处方式', character_context: '角色资料与梗', conversation_style: '说话方式'}
const example = ref({content: "", context: "", scene_id: ""})
const persona = ref({ identity_name: '', identity_persona: '', identity_core: '', conversation_style: '', character_context: '' })
const onebotForm = ref({
  connection_mode: 'forward_ws', action_transport: 'websocket',
  ws_url: 'ws://127.0.0.1:13001/', http_url: 'http://127.0.0.1:13000/',
  host: '127.0.0.1', port: 8080, access_token: '',
})
const shadow = ref({ enabled: false, would_send: [] })
const annotations = ref({ annotations: [], stats: { TP: 0, FP: 0, TN: 0, FN: 0 }, total: 0, accuracy: 0, precision: 0 })
const form = ref({ current_password: '', new_password: '' })
const message = ref('')
const error = ref('')

onMounted(load)
async function load() {
  try {
    exemplars.value = (await api("/api/voice/exemplars")).exemplars
    me.value = await api('/api/auth/me')
    const personaRes = await api('/api/settings/persona')
    persona.value = {
      identity_name: personaRes.identity_name,
      identity_persona: personaRes.identity_persona,
      identity_core: personaRes.identity_core,
      character_context: personaRes.character_context || '',
      conversation_style: personaRes.conversation_style || '',
    }
    const onebotRes = await api('/api/websocket/status')
    onebot.value = onebotRes
    onebotForm.value = {
      connection_mode: onebotRes.connection_mode,
      action_transport: onebotRes.action_transport,
      ws_url: onebotRes.ws_url,
      http_url: onebotRes.http_url,
      host: onebotRes.host,
      port: onebotRes.port,
      access_token: '',
    }
    const shadowRes = await api('/api/cockpit/shadow')
    shadow.value.enabled = !!shadowRes.enabled
    shadow.value.would_send = shadowRes.would_send || []

    const annRes = await api('/api/cockpit/shadow-annotations')
    annotations.value = annRes
  } catch (e) {
    error.value = e.message
  }
}

async function saveExample() {
  try {
    await api('/api/voice/exemplars' + (editingExample.value ? '/' + editingExample.value : ''),
      {method: editingExample.value ? 'PUT' : 'POST', body: JSON.stringify(example.value)})
    editingExample.value = null
    example.value = {content: '', context: '', scene_id: ''}
    await load()
  } catch (e) { error.value = e.message }
}
function editExample(item) {
  editingExample.value = item.id
  example.value = {content: item.content, context: item.context, scene_id: item.scene_id || '', tag: item.tag}
}
async function changeExample(item, remove = false) {
  if (remove && !confirm('删除这条表达样例？')) return
  try {
    await api(remove ? '/api/voice/exemplars/' + item.id : '/api/voice/exemplars/toggle',
      {method: remove ? 'DELETE' : 'POST', body: remove ? undefined : JSON.stringify({example_id: item.id, enabled: !item.enabled})})
    await load()
  } catch (e) { error.value = e.message }
}
async function savePersona() {
  error.value = ''
  message.value = ''
  try {
    const res = await api('/api/settings/persona', {
      method: 'POST',
      body: JSON.stringify(persona.value),
    })
    message.value = res.message
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function previewDiana() {
  try { presetPreview.value = await api('/api/settings/persona/diana') }
  catch (e) { error.value = e.message }
}
async function applyDiana() {
  try {
    const result = await api('/api/settings/persona/diana', {method: 'POST',
      body: JSON.stringify({preview_token: presetPreview.value.preview_token})})
    message.value = result.message
    presetPreview.value = null
    await load()
  } catch (e) { error.value = e.message }
}

async function saveOneBot(showSuccess = true) {
  error.value = ''
  message.value = ''
  try {
    const body = { ...onebotForm.value }
    if (!body.access_token) body.access_token = null
    const res = await api('/api/websocket/config', { method: 'POST', body: JSON.stringify(body) })
    if (showSuccess) message.value = res.message
    await load()
    return true
  } catch (e) {
    error.value = e.message
    return false
  }
}

async function testOneBotHttp() {
  error.value = ''
  message.value = ''
  if (!await saveOneBot(false)) return
  try {
    const res = await api('/api/websocket/test-http', { method: 'POST' })
    message.value = res.message
  } catch (e) {
    error.value = e.message
  }
}

function onebotStatusText() {
  if (onebot.value?.connected) return '已经连接，可以收取消息'
  if (onebot.value?.connection_mode === 'forward_ws') return '正在等待连接成功，失败后会自动重试'
  return '正在监听，等待 OneBot 主动连接'
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

    <div class="panel persona-panel">
      <div class="panel-header">
        <div>
          <div class="bento-badge">机器人性格</div>
          <h2>人格与说话方式</h2>
          <p class="muted">这里写的是机器人长期稳定的性格，不需要填写技术提示词。</p>
        </div>
        <button class="primary" @click="savePersona">保存并立即生效</button>
        <button @click="previewDiana">预览新版嘉然人格</button>
      </div>
      <div v-if="presetPreview" class="panel">
        <h3>{{ presetPreview.applied ? '已应用过新版，后续编辑不会被覆盖' : '确认更新内容' }}</h3>
        <p class="muted">已保存的人工修改会保留。停用 {{ presetPreview.disable_example_ids.length }} 条未修改的旧预设样例，添加 12 组新样例；不删除聊天、任务或记忆。尚未保存的表单修改不参与比较。</p>
        <details v-for="field in presetPreview.fields" :key="field.key">
          <summary>{{ personaLabels[field.key] }} · {{ field.action === 'update' ? '更新' : field.action === 'preserve' ? '保留人工修改' : '保持不变' }}</summary>
          <p style="white-space: pre-wrap">原内容：{{ field.current || '未设置' }}</p>
          <p style="white-space: pre-wrap">应用后：{{ field.next }}</p>
        </details>
        <button v-if="!presetPreview.applied" class="primary" @click="applyDiana">确认应用</button>
        <button @click="presetPreview = null">关闭预览</button>
      </div>
      <div class="persona-fields">
        <label>机器人名字
          <input v-model="persona.identity_name" placeholder="例如：Len" />
        </label>
        <label class="wide">身份背景
          <textarea v-model="persona.identity_persona" rows="5" placeholder="例如：嘴有点损但没有恶意，熟人面前话多，对比赛和直播很感兴趣……"></textarea>
          <small>写性格、兴趣、价值倾向，以及它在群里的常见角色。</small>
        </label>
        <label class="wide">性格与相处方式
          <textarea v-model="persona.identity_core" rows="5" placeholder="例如：先弄清楚大家在聊什么；熟人遇到困难会记在心里；不确定就直说。"></textarea>
          <small>这是贯穿聊天、查资料和履约的核心人格。</small>
        </label>
        <label class="wide">角色资料与梗
          <textarea v-model="persona.character_context" rows="8" placeholder="角色背景、梗的语境与资料日期；不是群聊记忆"></textarea>
        </label>
        <label class="wide">希望它怎么说话
          <textarea v-model="persona.conversation_style" rows="4" placeholder="例如：短句、口语化，可以接梗，不写长篇解释，不用客服腔……"></textarea>
          <small>这里只控制表达习惯，不会绕过运行时安全和发送限制。</small>
        </label>
      </div>
    </div>

    <div class="panel persona-panel">
      <h2>表达样例</h2>
      <p class="muted">启用的全局及本群样例按固定顺序提供，不轮换抽取。它们只示范语气，不是真实聊天或记忆。</p>
      <form class="persona-fields" @submit.prevent="saveExample">
        <label>前文与语境<textarea v-model="example.context" rows="3" placeholder="写清楚是谁在和谁说话"></textarea></label>
        <label>群聊范围<input v-model="example.scene_id" placeholder="留空适用于所有群；或填 group:群号" /></label>
        <label class="wide">理想的说法<textarea required v-model="example.content" rows="2"></textarea></label>
        <button class="primary">{{ editingExample ? '保存修改' : '添加样例' }}</button>
        <button v-if="editingExample" type="button" @click="editingExample = null; example = {content: '', context: '', scene_id: ''}">取消编辑</button>
      </form>
      <div v-for="item in exemplars" :key="item.id" class="kv">
        <span>{{ item.context || '通用表达' }} → {{ item.content }} <small class="muted">{{ item.scene_id || '所有群' }}</small></span>
        <div class="action-btn-group">
          <button @click="editExample(item)">编辑</button>
          <button @click="changeExample(item)">{{ item.enabled ? '停用' : '启用' }}</button>
          <button class="danger" @click="changeExample(item, true)">删除</button>
        </div>
      </div>
    </div>

    <div class="panel onebot-panel" v-if="onebot">
      <div class="panel-header">
        <div>
          <div class="bento-badge">QQ 连接</div>
          <h2>连接 OneBot</h2>
          <p class="muted">{{ onebotStatusText() }}</p>
          <p v-if="onebot.self_id" class="muted">已自动识别机器人 QQ：{{ onebot.self_id }}</p>
        </div>
        <span class="tag" :class="onebot.connected ? 'ok' : 'warn'">{{ onebot.connected ? '已连接' : '未连接' }}</span>
      </div>

      <div class="connection-mode-grid">
        <label class="choice-card" :class="{ selected: onebotForm.connection_mode === 'forward_ws' }">
          <input v-model="onebotForm.connection_mode" type="radio" value="forward_ws" />
          <span><strong>主动连接 OneBot</strong><small>适合 OneBot 已经监听连接端口的情况</small></span>
        </label>
        <label class="choice-card" :class="{ selected: onebotForm.connection_mode === 'reverse_ws' }">
          <input v-model="onebotForm.connection_mode" type="radio" value="reverse_ws" />
          <span><strong>等待 OneBot 连接</strong><small>由 LenBot 监听端口，等待 OneBot 接入</small></span>
        </label>
      </div>

      <div class="connection-fields">
        <template v-if="onebotForm.connection_mode === 'forward_ws'">
          <label>消息连接地址<input v-model="onebotForm.ws_url" required placeholder="ws://127.0.0.1:13001/" /></label>
        </template>
        <template v-else>
          <label>监听地址<input v-model="onebotForm.host" required placeholder="127.0.0.1" /></label>
          <label>监听端口<input v-model.number="onebotForm.port" type="number" min="1" max="65535" /></label>
        </template>
        <label>发送消息的方式
          <select v-model="onebotForm.action_transport">
            <option value="websocket">使用消息连接收发</option>
            <option value="http">使用 HTTP 接口发送</option>
          </select>
        </label>
        <label>HTTP 接口地址<input v-model="onebotForm.http_url" :required="onebotForm.action_transport === 'http'" placeholder="http://127.0.0.1:13000/" /></label>
        <label class="wide">访问令牌
          <input v-model="onebotForm.access_token" type="password" :placeholder="onebot.access_token_set ? '已保存，留空不会修改' : '从 OneBot 面板复制访问令牌'" />
        </label>
      </div>
      <p v-if="onebot.last_error" class="notice error">最近一次连接失败：{{ onebot.last_error }}</p>
      <div class="action-btn-group connection-actions">
        <button class="primary" @click="saveOneBot()">保存并重新连接</button>
        <button @click="testOneBotHttp">测试 HTTP 接口</button>
      </div>
    </div>

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

.connection-mode-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 16px 0;
}
.choice-card {
  min-height: 82px;
  padding: 14px;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  border: 1px solid var(--border-light);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.55);
  cursor: pointer;
}
.choice-card.selected {
  border-color: var(--border-accent);
  background: var(--accent-soft);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.07);
}
.choice-card input {
  width: 18px;
  min-height: 18px;
  margin-top: 2px;
}
.choice-card strong, .choice-card small {
  display: block;
}
.choice-card strong {
  color: var(--text);
}
.choice-card small {
  margin-top: 5px;
  color: var(--muted);
  line-height: 1.45;
}
.connection-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 13px;
}
.connection-fields label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  color: var(--text-soft);
  font-size: 0.86rem;
  font-weight: 650;
}
.connection-fields .wide {
  grid-column: 1 / -1;
}
.connection-actions {
  margin-top: 15px;
}
.persona-panel {
  margin-bottom: 24px;
}
.persona-fields {
  display: grid;
  grid-template-columns: minmax(220px, 0.45fr) 1fr;
  gap: 14px;
}
.persona-fields label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  color: var(--text-soft);
  font-size: 0.86rem;
  font-weight: 650;
}
.persona-fields .wide {
  grid-column: 1 / -1;
}
.persona-fields small {
  color: var(--muted);
  font-weight: 400;
  line-height: 1.5;
}
.persona-fields textarea {
  resize: vertical;
  min-height: 96px;
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

@media (max-width: 600px) {
  .connection-mode-grid, .connection-fields {
    grid-template-columns: 1fr;
  }
  .persona-fields {
    grid-template-columns: 1fr;
  }
  .connection-fields .wide {
    grid-column: auto;
  }
}
</style>
