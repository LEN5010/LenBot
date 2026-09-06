<script setup>
import { ref, onMounted } from 'vue'
import { api, fmtTime } from '../api.js'

const me = ref(null)
const onebot = ref(null)
const exemplars = ref([])
const editingExample = ref(null)
const presetPreview = ref(null)
const personaLabels = {identity_name: '机器人名字', identity_persona: '身份背景', identity_core: '性格与相处方式', character_context: '角色资料与梗', conversation_style: '说话方式'}
const emptyExample = () => ({ context: '', scene_id: '', tag: '', segments: [{ type: 'text', text: '' }] })
const example = ref(emptyExample())
const exampleMedia = ref([]), mediaQuery = ref(''), exampleSaving = ref(false)
const persona = ref({ identity_name: '', identity_persona: '', identity_core: '', conversation_style: '', character_context: '' })
const addressNames = ref('')
const onebotForm = ref({
  connection_mode: 'forward_ws', action_transport: 'websocket',
  ws_url: 'ws://127.0.0.1:13001/', http_url: 'http://127.0.0.1:13000/',
  host: '127.0.0.1', port: 8080, access_token: '',
})
const shadow = ref(null)
const allowedGroups = ref('')
const form = ref({ current_password: '', new_password: '' })
const message = ref('')
const error = ref('')
const resetting = ref(false)
const resetConfirming = ref(false)
const resetFeedback = ref('')
const resetFailed = ref(false)

onMounted(load)
async function load() {
  try {
    exemplars.value = (await api("/api/voice/exemplars")).exemplars
    await loadExampleMedia()
    me.value = await api('/api/auth/me')
    const personaRes = await api('/api/settings/persona')
    persona.value = {
      identity_name: personaRes.identity_name,
      identity_persona: personaRes.identity_persona,
      identity_core: personaRes.identity_core,
      character_context: personaRes.character_context || '',
      conversation_style: personaRes.conversation_style || '',
    }
    addressNames.value = personaRes.address_names.join('、')
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
    shadow.value = shadowRes
    allowedGroups.value = shadowRes.allowed_scenes.map(scene => scene.replace(/^group:/, '')).join('\n')
  } catch (e) {
    error.value = e.message
  }
}

async function saveExample() {
  error.value = ''; exampleSaving.value = true
  try {
    await api('/api/voice/exemplars' + (editingExample.value ? '/' + editingExample.value : ''),
      {method: editingExample.value ? 'PUT' : 'POST', body: JSON.stringify(example.value)})
    editingExample.value = null
    example.value = emptyExample()
    message.value = '表达样例已保存'
    exemplars.value = (await api('/api/voice/exemplars')).exemplars
    await loadExampleMedia()
  } catch (e) { error.value = e.message } finally { exampleSaving.value = false }
}
function editExample(item) {
  editingExample.value = item.id
  example.value = { context: item.context, scene_id: item.scene_id || '', tag: item.tag, segments: item.segments.map(part => ({ ...part })) }
  loadExampleMedia()
}
function cancelExample() { editingExample.value = null; example.value = emptyExample(); mediaQuery.value = ''; loadExampleMedia() }
function addExamplePart(type) { example.value.segments.push(type === 'text' ? { type, text: '' } : { type, asset_id: '' }) }
function changeExamplePart(index, type) { example.value.segments[index] = type === 'text' ? { type, text: '' } : { type, asset_id: '' } }
function moveExamplePart(index, direction) {
  const parts = example.value.segments, target = index + direction
  if (target >= 0 && target < parts.length) [parts[index], parts[target]] = [parts[target], parts[index]]
}
async function loadExampleMedia() {
  try {
    const params = new URLSearchParams({ scene_id: example.value.scene_id.trim() || 'global-safe', query: mediaQuery.value })
    exampleMedia.value = (await api('/api/media?' + params.toString())).filter(asset => asset.curated && asset.enabled)
  } catch (e) { error.value = e.message }
}
function exampleImage(assetId, sceneId = '') { return `/api/media/${encodeURIComponent(assetId)}/file?scene_id=${encodeURIComponent(sceneId || 'global-safe')}` }
async function changeExample(item, remove = false) {
  if (remove && !confirm('删除这条表达样例？')) return
  try {
    await api(remove ? '/api/voice/exemplars/' + item.id : '/api/voice/exemplars/toggle',
      {method: remove ? 'DELETE' : 'POST', body: remove ? undefined : JSON.stringify({example_id: item.id, enabled: !item.enabled})})
    exemplars.value = (await api('/api/voice/exemplars')).exemplars
    if (remove && editingExample.value === item.id) cancelExample()
  } catch (e) { error.value = e.message }
}
async function savePersona() {
  error.value = ''
  message.value = ''
  try {
    const res = await api('/api/settings/persona', {
      method: 'POST',
      body: JSON.stringify({ ...persona.value,
        address_names: [...new Set(addressNames.value.split(/[\n,，、]+/).map(name => name.trim()).filter(Boolean))],
      }),
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
      ? '已开启仅观察，机器人不会真实发送消息'
      : '已关闭仅观察，机器人可向名单内的群真实发送消息'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function saveAllowedGroups() {
  error.value = ''
  message.value = ''
  const groups = [...new Set(allowedGroups.value.split(/[,，\s]+/).filter(Boolean))]
  if (groups.some(group => !/^[1-9]\d*$/.test(group))) {
    error.value = '请填写有效的 QQ 群号，多个群号用逗号或换行分隔'
    return
  }
  try {
    const res = await api('/api/cockpit/shadow/scenes', {
      method: 'POST',
      body: JSON.stringify({ scene_ids: groups.map(group => 'group:' + group) }),
    })
    shadow.value.allowed_scenes = res.allowed_scenes
    allowedGroups.value = res.allowed_scenes.map(scene => scene.replace(/^group:/, '')).join('\n')
    message.value = '允许实发的群已保存'
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

async function resetConversationData() {
  if (resetting.value) return
  resetting.value = true
  resetFailed.value = false
  resetFeedback.value = '正在停止旧任务并清空对话数据…'
  try {
    await api('/api/settings/reset', { method: 'POST' })
    resetFeedback.value = '全部对话数据已清空，可以重新开始聊天'
    await load()
  } catch (e) {
    resetFailed.value = true
    resetFeedback.value = e.message
  } finally {
    resetting.value = false
    resetConfirming.value = false
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
        <p class="muted">已保存的人工修改会保留。停用 {{ presetPreview.disable_example_ids.length }} 条未修改的旧预设样例，添加 {{ presetPreview.example_count }} 组新样例；不删除聊天、任务或记忆。尚未保存的表单修改不参与比较。</p>
        <details v-for="field in presetPreview.fields" :key="field.key">
          <summary>{{ personaLabels[field.key] }} · {{ field.action === 'update' ? '更新' : field.action === 'preserve' ? '保留人工修改' : '保持不变' }}</summary>
          <p style="white-space: pre-wrap">原内容：{{ field.current || '未设置' }}</p>
          <p style="white-space: pre-wrap">应用后：{{ field.next }}</p>
        </details>
        <details><summary>查看表达样例</summary><article v-for="item in presetPreview.examples" :key="item.id" class="example-preview"><p class="example-context">{{ item.context }}</p><div class="example-body"><template v-for="(part, index) in item.segments" :key="index"><span v-if="part.type === 'text'">{{ part.text }}</span><img v-else :src="exampleImage(part.asset_id)" alt="预设表情样例" /></template></div><p v-if="item.missing_media_refs?.length" class="muted">{{ item.content }}</p></article></details>
        <p v-if="presetPreview.missing_media?.length" class="notice error">固定目录缺少这些情绪的运营素材：{{ presetPreview.missing_media.join('、') }}。选入对应素材后重新预览。</p>
        <button v-if="!presetPreview.applied" class="primary" :disabled="!!presetPreview.missing_media?.length" @click="applyDiana">确认应用</button>
        <button @click="presetPreview = null">关闭预览</button>
      </div>
      <div class="persona-fields">
        <label>机器人名字
          <input v-model="persona.identity_name" placeholder="例如：Len" />
        </label>
        <label>呼唤昵称
          <input v-model="addressNames" placeholder="然比、小然" />
          <small>用逗号或顿号分隔。叫名字、@或引用时优先回应，别人之间的闲聊通常旁听；接着聊不必重复叫名字。</small>
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
      <p class="muted">启用的全局及本群样例按固定顺序提供，可使用文字、单图或图文混排。它们示范表达，不是真实聊天或记忆。</p>
      <form class="persona-fields" @submit.prevent="saveExample">
        <label>前文与语境<textarea v-model="example.context" rows="3" placeholder="写清楚是谁在和谁说话"></textarea></label>
        <label>群聊范围<input v-model="example.scene_id" placeholder="留空适用于所有群；或填 group:群号" /></label>
        <div class="wide"><div class="toolbar"><strong>理想表达</strong><button type="button" @click="addExamplePart('text')">添加文字</button><button type="button" @click="addExamplePart('image')">添加图片</button></div>
          <div v-for="(part, index) in example.segments" :key="index" class="example-part"><div class="toolbar"><span class="muted">第 {{ index + 1 }} 段</span><select :value="part.type" @change="changeExamplePart(index, $event.target.value)"><option value="text">文字</option><option value="image">图片</option></select><button type="button" :disabled="index === 0" @click="moveExamplePart(index, -1)">上移</button><button type="button" :disabled="index === example.segments.length - 1" @click="moveExamplePart(index, 1)">下移</button><button type="button" @click="example.segments.splice(index, 1)">移除</button></div>
            <textarea v-if="part.type === 'text'" v-model="part.text" rows="2" required placeholder="在这个语境下实际要说的话" />
            <template v-else><select v-model="part.asset_id" required><option value="">选择当前范围内的运营素材</option><option v-if="part.asset_id && !exampleMedia.some(asset => asset.id === part.asset_id)" :value="part.asset_id">当前素材 · {{ part.asset_id }}</option><option v-for="asset in exampleMedia" :key="asset.id" :value="asset.id">{{ asset.description || asset.id }} · {{ asset.scope }}</option></select><img v-if="part.asset_id" class="example-image" :src="exampleImage(part.asset_id, example.scene_id)" alt="样例图片预览" /></template>
          </div>
          <div class="toolbar"><input v-model="mediaQuery" placeholder="素材描述或标签" /><button type="button" @click="loadExampleMedia">查找运营素材</button></div><p class="muted">全局样例只能使用 global-safe 运营素材。本群样例也可使用对应群的运营素材。</p>
        </div>
        <button class="primary" :disabled="exampleSaving || !example.segments.length">{{ exampleSaving ? '正在保存…' : editingExample ? '保存修改' : '添加样例' }}</button>
        <button v-if="editingExample" type="button" @click="cancelExample">取消编辑</button>
      </form>
      <div v-for="item in exemplars" :key="item.id" class="example-row">
        <div class="example-preview"><p class="example-context">{{ item.context || '通用表达' }}</p><div class="example-body"><template v-for="(part, index) in item.segments" :key="index"><span v-if="part.type === 'text'">{{ part.text }}</span><img v-else :src="exampleImage(part.asset_id, item.scene_id)" alt="运营表达样例" loading="lazy" /></template></div><small class="muted">{{ item.scene_id || '所有群' }} · {{ item.enabled ? '已启用' : '已停用' }}</small><p v-if="item.available === false" class="tag warn">{{ item.unavailable_reason }}</p></div>
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

    <div v-if="shadow" class="panel">
      <div class="panel-header">
        <div>
          <h2>仅观察（Shadow）</h2>
          <p class="muted">开启时照常观察和思考，但不会真实发送。关闭后仅允许向下方名单内的群发送，名单外始终仅观察。</p>
        </div>
        <button :class="shadow.enabled ? 'primary' : 'danger'" @click="toggleShadow">
          {{ shadow.enabled ? '关闭仅观察' : '开启仅观察' }}
        </button>
      </div>
      <p><span class="tag" :class="shadow.enabled ? 'warn' : 'ok'">{{ shadow.enabled ? '当前：仅观察' : '当前：名单内允许实发' }}</span></p>
      <form class="form-vertical delivery-form" @submit.prevent="saveAllowedGroups">
        <label>允许实发的群
          <textarea v-model="allowedGroups" rows="3" placeholder="126300994"></textarea>
          <small class="muted">填写 QQ 群号，多个群号用逗号或换行分隔。留空表示所有群都仅观察。</small>
        </label>
        <button class="primary">保存群名单</button>
      </form>
    </div>

    <div class="panel" style="margin-top: 24px;">
      <h2>重新开始聊天</h2>
      <p class="muted">清空全部群聊和私聊的对话数据、记忆、任务与工作、工具资料、聊天图片及场景上下文。保留运营表情库、模型、OneBot、人格、表达样例、登录配置、Shadow 开关和实发群名单。</p>
      <button v-if="!resetConfirming" class="danger" @click="resetConfirming = true; resetFeedback = ''">Reset 对话数据</button>
      <div v-else>
        <p>确认清空全部对话数据？表情库和配置会保留。</p>
        <button class="danger" :disabled="resetting" @click="resetConversationData">{{ resetting ? '正在清空…' : '确认清空' }}</button>
        <button :disabled="resetting" @click="resetConfirming = false">取消</button>
      </div>
      <p v-if="resetFeedback" role="status" :class="resetFailed ? 'notice error' : 'notice success'">{{ resetFeedback }}</p>
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

.delivery-form {
  margin-top: 16px;
}
.delivery-form button {
  align-self: flex-start;
}
.delivery-form textarea {
  resize: vertical;
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

.highlight {
  color: var(--accent-strong);
  font-weight: 500;
}
.password-change-box {
  background: rgba(239, 246, 255, 0.66);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 14px 16px;
}
.example-part { padding:14px;margin:12px 0;background:rgba(239,246,255,.6);border:1px solid var(--border);border-radius:12px }
.example-part textarea,.example-part>select { width:100% }
.example-image { display:block;max-width:100%;max-height:180px;object-fit:contain;margin-top:12px }
.example-row { display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:start;padding:18px 0;border-bottom:1px solid var(--border) }
.example-context { white-space:pre-wrap;color:var(--muted);font-size:.86rem;line-height:1.6 }
.example-body { display:flex;flex-wrap:wrap;gap:10px;margin:10px 0;align-items:flex-start }
.example-body span { flex-basis:100%;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;color:var(--text) }
.example-body img { width:auto;max-width:160px;max-height:160px;object-fit:contain }
.example-preview { min-width:0 }

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
  .example-row { grid-template-columns:1fr }
}
</style>
