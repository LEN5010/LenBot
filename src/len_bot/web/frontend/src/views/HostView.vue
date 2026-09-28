<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import TurnRecordDetail from '../components/TurnRecordDetail.vue'

const route = useRoute(), router = useRouter()
const state = ref(null), scene = ref(null), detail = ref(null)
const stateError = ref(''), sceneError = ref(''), detailError = ref('')
const loading = ref(false), sceneLoading = ref(false), detailLoading = ref(false)
const selectedTurn = ref(null), detailHeading = ref(null)
const imageErrors = ref({})
const socketState = ref('connecting'), socketError = ref('')
const selectedScene = computed(() => route.query.scene)
const sceneOptions = computed(() => state.value ? state.value.scenes.map(item => ({
  title: `${sceneName(item.scene)} · ${item.persona.name}`, value: item.scene
})) : [])
const messages = computed(() => scene.value ? [...scene.value.messages].sort((a, b) => a.seq - b.seq) : [])
const turns = computed(() => scene.value ? [...scene.value.turns].sort((a, b) => b.started - a.started) : [])
let active = true, socket = null, refreshPending = false
let sceneRequest = 0, detailRequest = 0

function localTime(value) {
  return new Date(value * 1000).toLocaleString('zh-CN', {
    timeZone: state.value.timezone, timeZoneName: 'short', hour12: false
  })
}
function runtimeLabel(value) {
  return ({ created: '已装配', starting: '启动中', waiting_connection: '等待 OneBot 连接',
    running: '运行中', stopping: '停止中', stopped: '已停止' })[value] || value
}
function statusLabel(value) {
  return ({ received: '平台入站 · 已保存', sent: '平台已确认发送', simulated: '模拟表达 · 未发送到 QQ',
    failed: '发送失败', unconfirmed: '发送结果未确认' })[value] || value
}
function imageUrl(message, image) {
  return `/api/host/scenes/${encodeURIComponent(scene.value.scene)}/messages/${message.seq}/images/${image.image_index}`
}
function turnLabel(value) {
  return ({ queued: '等待执行', running: '正在执行', settling: '即将结束', settled: '已结束',
    error: '失败', timeout: '超时', cancelled: '已取消', interrupted: '已中断',
    step_limit: '达到轮次上限' })[value] || value
}

async function refreshDetail(focus = false) {
  if (!selectedTurn.value) return
  const request = ++detailRequest, id = selectedTurn.value, name = selectedScene.value
  detailLoading.value = true
  try {
    const result = await api(`/api/host/scenes/${encodeURIComponent(name)}/turns/${encodeURIComponent(id)}`)
    if (!active || request !== detailRequest) return
    detail.value = result
    detailError.value = ''
    if (focus) {
      await nextTick()
      detailHeading.value?.focus({ preventScroll: true })
    }
  } catch (error) {
    if (active && request === detailRequest) detailError.value = error.message
  } finally {
    if (active && request === detailRequest) detailLoading.value = false
  }
}

async function refreshScene() {
  if (selectedScene.value == null) return
  const request = ++sceneRequest
  if (typeof selectedScene.value !== 'string') {
    sceneError.value = '场景地址必须只指定一个 scene 参数。'
    return
  }
  sceneLoading.value = true
  try {
    const result = await api(`/api/host/scenes/${encodeURIComponent(selectedScene.value)}`)
    if (!active || request !== sceneRequest) return
    scene.value = result
    sceneError.value = ''
    if (selectedTurn.value) await refreshDetail()
  } catch (error) {
    if (active && request === sceneRequest) sceneError.value = error.message
  } finally {
    if (active && request === sceneRequest) sceneLoading.value = false
  }
}

// Changes during a read request cause one following read, not parallel snapshots.
async function refresh() {
  refreshPending = true
  if (loading.value) return
  loading.value = true
  try {
    while (active && refreshPending) {
      refreshPending = false
      try {
        const result = await api('/api/host/state')
        if (!active) return
        state.value = result
        stateError.value = ''
        if (selectedScene.value == null) {
          await router.replace({ name: route.name, query: { scene: result.scenes[0].scene } })
        } else {
          await refreshScene()
        }
      } catch (error) {
        if (active) stateError.value = error.message
      }
    }
  } finally {
    if (active) loading.value = false
  }
}

function selectScene(value) {
  router.push({ name: route.name, query: { scene: value } })
}
function selectTurn(turn) {
  ++detailRequest
  detail.value = null
  detailError.value = ''
  detailLoading.value = false
  selectedTurn.value = selectedTurn.value === turn.id ? null : turn.id
  if (selectedTurn.value) refreshDetail(true)
}
watch(selectedScene, () => {
  ++sceneRequest
  ++detailRequest
  scene.value = null
  imageErrors.value = {}
  detail.value = null
  selectedTurn.value = null
  sceneError.value = ''
  detailError.value = ''
  sceneLoading.value = false
  detailLoading.value = false
  refreshScene()
  openRequestedTurn()
})
// Other pages link to one actual turn, which may be older than the recent list.
function openRequestedTurn() {
  const id = route.query.turn
  if (typeof id !== 'string' || !id || selectedTurn.value === id) return
  selectedTurn.value = id
  refreshDetail(true)
}
watch(() => route.query.turn, openRequestedTurn)

function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return
  socketError.value = ''
  socketState.value = 'connecting'
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/host/events`)
  socket = connection
  connection.onopen = () => {
    if (active && socket === connection) socketState.value = 'connected'
  }
  connection.onmessage = event => {
    if (!active || socket !== connection) return
    try {
      const notice = JSON.parse(event.data)
      if (notice?.type !== 'changed') throw new Error('未知的变化通知')
      refresh()
    } catch (error) {
      socketError.value = `变化通知无法读取：${error.message}`
      connection.close()
    }
  }
  connection.onerror = () => {
    if (active && socket === connection) socketError.value = '页面实时连接出错；不会自动重连。'
  }
  connection.onclose = () => {
    if (active && socket === connection) socketState.value = 'disconnected'
  }
}
function reconnect() {
  socket?.close()
  socket = null
  connect()
}
onMounted(() => { refresh(); connect(); openRequestedTurn() })
onBeforeUnmount(() => {
  active = false
  ++sceneRequest
  ++detailRequest
  socket?.close()
})
</script>

<template>
  <div class="page-stack host-view">
    <header class="page-intro">
      <div><p class="eyebrow">LenBot 运行管理</p><h1>{{ route.name === "host-logs" ? "日志" : "群聊" }}</h1><p v-if="route.name === 'host-logs'" class="muted">当前提供消息与轮次时间线；系统日志尚未接入本页。</p>
        <p class="muted">查看当前连接、聊天原话与实际调用。本页只读；工具范围、模型与场景设置从下方入口管理。</p>
        <div class="management-links">
          <v-btn variant="text" :to="{name:'host-capabilities',query:route.query}">工具能力</v-btn>
          <v-btn variant="text" :to="{name:'host-models'}">模型配置</v-btn>
          <v-btn variant="text" :to="{name:'host-settings',query:route.query}">场景与角色</v-btn>
        </div></div>
      <v-btn variant="outlined" :loading="loading" @click="refresh">读取最新记录</v-btn>
    </header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert"
      :title="state ? '读取宿主失败 · 保留上次记录' : '读取宿主失败'">{{ stateError }}</v-alert>
    <v-alert v-if="socketState==='disconnected' || socketError" type="warning" variant="tonal" role="alert">
      {{ socketError || '页面实时连接已断开；下方为上次读取记录，不表示宿主仍在线。' }}
      <div class="mt-2"><v-btn variant="outlined" @click="reconnect">重新连接实时更新</v-btn></div>
    </v-alert>
    <p v-else class="muted live-status" role="status">{{ socketState==='connected'?'页面实时更新已连接':'正在连接页面实时更新…' }}</p>
    <div v-if="!state && loading" class="surface empty-state" role="status">正在读取宿主的实际状态…</div>
    <section v-if="state" class="surface" aria-labelledby="host-status-title">
      <div class="section-heading"><h2 id="host-status-title">宿主最近状态</h2>
        <v-chip variant="tonal" :color="state.connection.connected?'success':'warning'">{{ runtimeLabel(state.connection.status) }}</v-chip></div>
      <dl class="facts">
        <div><dt>消息出口</dt><dd>{{ state.delivery==='simulated'?'模拟落库 · 不发送到 QQ':'OneBot 实际发送出口' }}</dd></div>
        <div><dt>OneBot WebSocket</dt><dd>{{ state.connection.connected?'已连接':'未连接' }} · {{ state.connection.mode==='reverse_ws'?'反向连接':'正向连接' }}</dd></div>
        <div><dt>Bot QQ</dt><dd>{{ state.bot_qq }}</dd></div>
        <div><dt>业务时区</dt><dd>{{ state.timezone }}</dd></div>
      </dl>
      <p class="muted connection-note">WebSocket 连接状态不代表 QQ 客户端已登录；单条发送结果以该条保存的回执为准。</p>
      <p v-if="state.connection.addresses.length" class="muted address">监听地址：{{ state.connection.addresses.map(address=>JSON.stringify(address)).join(' · ') }}</p>
      <v-alert v-if="state.connection.last_error" type="warning" variant="tonal" class="mt-4" title="最近一次平台错误 · 重连后保留原文"><pre class="error-text">{{ state.connection.last_error }}</pre></v-alert>
    </section>
    <section v-if="state" class="surface" aria-labelledby="scene-title">
      <div class="section-heading"><h2 id="scene-title">配置场景</h2><span class="muted">{{ state.scenes.length }} 个 · 只显示当前根配置中的场景</span></div>
      <v-select :model-value="selectedScene" :items="sceneOptions" label="选择场景" hide-details @update:model-value="selectScene" />
      <v-alert v-if="sceneError" type="error" variant="tonal" class="mt-4" role="alert"
        :title="scene ? '读取场景失败 · 保留本场景上次记录' : '读取场景失败'">{{ sceneError }}</v-alert>
      <p v-if="sceneLoading" class="mt-4" role="status">正在读取场景…</p>
      <dl v-if="scene" class="facts scene-facts">
        <div><dt>场景</dt><dd>{{ scene.scene }}</dd></div>
        <div><dt>角色</dt><dd>{{ scene.persona.name }} <small class="muted">{{ scene.persona.id }}</small></dd></div>
        <div><dt>大脑 / 表达器</dt><dd>{{ scene.models.mind }} / {{ scene.models.voice }}</dd></div>
        <div><dt>表达方式</dt><dd>{{ scene.voice_mode==='direct'?'直接表达':'表达器组织台词' }}</dd></div>
      </dl>
    </section>
    <div v-if="scene" class="records-grid">
      <section class="surface" aria-labelledby="messages-title">
        <div class="section-heading"><h2 id="messages-title">最近消息</h2><span class="muted">最多 50 条 · 完整原文</span></div>
        <p v-if="!messages.length" class="empty-state">本场景尚无已保存消息。</p>
        <ol v-else class="record-list">
          <li v-for="message in messages" :key="message.seq" class="message-item" :class="{'own-message':message.is_self}">
            <div class="message-meta"><strong>{{ message.is_self?'Bot':(message.sender.nickname || message.sender.uid) }}</strong>
              <span>QQ {{ message.sender.uid }}</span><span>{{ localTime(message.time) }}</span></div>
            <div class="message-text">{{ message.rendered }}</div>
            <p class="muted message-status">{{ statusLabel(message.send_status) }}</p>
            <div v-if="message.images.length" class="message-images">
              <div v-for="image in message.images" :key="image.image_index" class="saved-image">
                <a :href="imageUrl(message,image)" target="_blank" rel="noopener" :aria-label="`打开已保存图片原件：${image.description || image.file}`">
                  <span v-if="imageErrors[`${message.seq}:${image.image_index}`]" class="image-error" role="status">已保存图片当前不可读取；打开链接可查看接口错误。</span>
                  <img v-else :src="imageUrl(message,image)" :alt="image.description || image.file" loading="lazy" :width="image.width" :height="image.height" @error="imageErrors[`${message.seq}:${image.image_index}`]=true" />
                </a>
                <p class="muted">{{ image.description || image.file }} · {{ image.mime_type }}<span v-if="image.animated"> · 动图</span></p>
              </div>
              <p class="muted media-fact">这里读取已保存的本地原件；发送状态以上方真实回执为准，平台 API 确认也不代表客户端已收到。</p>
            </div>
          </li>
        </ol>
      </section>
      <section class="surface" aria-labelledby="turns-title">
        <div class="section-heading"><h2 id="turns-title">对话轮次</h2><span class="muted">最近 20 轮</span></div>
        <p v-if="!turns.length" class="empty-state">尚无轮次。已保存消息不一定唤醒模型。</p>
        <ul v-else class="record-list">
          <li v-for="turn in turns" :key="turn.id" class="turn-item">
            <div class="turn-summary"><div><strong>{{ turnLabel(turn.status) }}</strong><span class="muted">{{ localTime(turn.started) }}</span></div>
              <v-btn variant="text" :aria-expanded="selectedTurn===turn.id" @click="selectTurn(turn)">{{ selectedTurn===turn.id?'收起经过':'查看经过' }}</v-btn></div>
            <pre v-if="turn.error" class="error-text">{{ turn.error }}</pre>
          </li>
        </ul>
        <div v-if="selectedTurn" class="turn-detail">
          <div class="section-heading"><h3 ref="detailHeading" tabindex="-1">轮次经过</h3>
            <v-btn variant="outlined" :loading="detailLoading" @click="refreshDetail()">重新读取本轮</v-btn></div>
          <p v-if="detailLoading" role="status">正在读取该轮的实际记录…</p>
          <v-alert v-if="detailError" type="error" variant="tonal" role="alert"
            :title="detail ? '读取失败 · 保留上次详情' : '读取轮次失败'">{{ detailError }}</v-alert>
          <TurnRecordDetail v-if="detail" :detail="detail" :timezone="scene.timezone" />
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.host-view{max-width:1480px;margin-inline:auto}
.page-intro{display:flex;justify-content:space-between;align-items:flex-start;gap:24px}
.page-intro h1{font-size:28px;line-height:1.25;margin:4px 0 12px}
.page-intro p{max-width:760px;margin-bottom:0}
.page-intro>.v-btn{flex:none}
.management-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0}
.live-status{font-size:13px;margin:0}
.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.section-heading h2{font-size:18px;line-height:1.4;margin:0}
.section-heading h3{font-size:16px;margin:0}
.section-heading>span{font-size:12px}
.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}
.facts>div{min-width:0}
.facts dt{font-size:12px;color:var(--muted);margin-bottom:4px}
.facts dd{font-size:14px;font-weight:600;overflow-wrap:anywhere}
.connection-note,.address{font-size:13px;margin:16px 0 0;overflow-wrap:anywhere}
.scene-facts{margin-top:20px}
.records-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;align-items:start}
.surface{min-width:0}
.record-list{list-style:none;padding:0;margin:0;display:grid;gap:12px}
.message-item{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.own-message{background:var(--bot-avatar-bg)}
.message-meta{display:flex;gap:8px 16px;flex-wrap:wrap;color:var(--muted);font-size:12px}
.message-meta strong{color:var(--ink);font-size:14px}
.message-text{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto;line-height:1.75}
.message-status{font-size:12px;border-top:1px solid var(--line);padding-top:8px;margin:0}
.message-images{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}.saved-image{width:min(100%,220px);min-width:0;overflow-wrap:anywhere}
.saved-image>a{display:grid;place-items:center;width:100%;height:180px;border:1px solid var(--line);border-radius:8px;background:var(--list-heading-bg);overflow:hidden}
.saved-image>a:focus-visible{outline:3px solid var(--primary);outline-offset:2px}
.saved-image img{display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain}
.saved-image p{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:5px 0}.image-error{color:var(--error-text);font-size:12px;padding:12px;text-align:center}
.media-fact{flex-basis:100%;font-size:12px;margin:0}
.turn-item{border-bottom:1px solid var(--line);padding-bottom:10px;min-width:0}
.turn-summary,.turn-summary>div{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.turn-summary{justify-content:space-between}
.turn-summary>div{align-items:baseline}
.turn-detail{border:1px solid var(--line);border-radius:10px;background:var(--list-heading-bg);padding:16px;margin-top:20px;min-width:0}
.error-text{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;color:var(--error-text)}
.v-btn{min-height:44px}
@media(max-width:1100px){.facts{grid-template-columns:repeat(2,minmax(0,1fr))}.records-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:600px){.page-intro{display:grid;gap:16px}.page-intro h1{font-size:24px}.facts{grid-template-columns:minmax(0,1fr);gap:12px}.turn-detail{padding:12px}}
</style>
