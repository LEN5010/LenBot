<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api.js'

const state = ref(null)
const loading = ref(false)
const stateError = ref('')
const socketState = ref('connecting')
const socketError = ref('')
const submitting = ref(false)
const submitError = ref('')
const receipt = ref(null)
const attempted = ref(false)
const form = ref({ uid: '', nickname: '', text: '', mention_bot: true, reply_to: null })
const selectedTurnId = ref(null)
const detail = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const detailHeading = ref(null)
let socket = null
let active = true
let stateRequest = 0
let detailRequest = 0

const isPrivate = computed(() => state.value?.scene?.startsWith('private:') || false)
const messages = computed(() => [...(state.value?.messages || [])].sort((a, b) => a.seq - b.seq))
const turns = computed(() => [...(state.value?.turns || [])].sort((a, b) => b.started - a.started))
const activeTurns = computed(() => turns.value.filter(turn => turn.ended == null))
const detailCalls = computed(() => (detail.value?.calls || []).map(call => {
  const nativeCalls = call.error == null && call.response?.finish_reason === 'tool_calls'
    ? call.response.message.tool_calls : []
  const tools = nativeCalls.map(native => ({
    id: native.id,
    name: native.function.name,
    arguments: native.function.arguments,
    result: call.tool_results === null ? null
      : call.tool_results.find(saved => saved.tool_call_id === native.id),
    hasDirectAssociation: call.tool_results !== null
  }))
  return { ...call, tools }
}))
const replyOptions = computed(() => messages.value
  .filter(message => message.platform_message_id != null)
  .map(message => {
    const rendered = message.rendered.replace(/\s+/g, ' ').trim()
    return {
      title: rendered.length > 120 ? `${rendered.slice(0, 119)}…` : rendered,
      value: message.platform_message_id
    }
  }))
const uidError = computed(() => /^[1-9][0-9]*$/.test(form.value.uid) ? '' : '填写正十进制 QQ 号')
const nicknameError = computed(() => form.value.nickname.trim() ? '' : '填写虚拟昵称')
const textError = computed(() => form.value.text.trim() ? '' : '填写消息正文')
const canSubmit = computed(() => !!state.value?.running && !submitting.value &&
  !uidError.value && !nicknameError.value && !textError.value)
const statusDisplay = computed(() => {
  if (stateError.value) return { text: state.value ? '读取失败 · 历史记录保留' : '读取失败', color: 'warning' }
  if (socketState.value !== 'connected') {
    const connection = socketState.value === 'connecting' ? '实时连接中' : '实时连接中断'
    return { text: state.value ? `${connection} · 历史记录保留` : connection, color: 'warning' }
  }
  if (!state.value) return { text: '正在读取测试状态', color: 'info' }
  return { text: `最近读取：${state.value.running ? '运行中' : '已停止'}`,
    color: state.value.running ? 'success' : 'warning' }
})

function localTime(value) {
  if (value == null) return '尚未结束'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    timeZone: state.value?.timezone || 'UTC', timeZoneName: 'short', hour12: false
  })
}
function statusLabel(status) {
  return ({ received: '虚拟入站 · 已保存', sent: '已确认发送', simulated: '模拟表达 · 未发送到 QQ',
    failed: '发送失败', unconfirmed: '发送结果未确认' })[status] || status
}
function turnLabel(status) {
  return ({ queued: '等待执行', running: '正在执行', settling: '即将结束', settled: '已结束',
    error: '失败', timeout: '超时', cancelled: '已取消', interrupted: '已中断',
    step_limit: '达到轮次上限' })[status] || status
}
function expressionDeliveryLabel(delivery) {
  return ({ simulated: '模拟表达已落库', sent: '平台已确认发送' })[delivery] || delivery
}
function receiptLabel(value) {
  if (value.status === 'queued') return '消息已保存并入队；这不是模型已回复。'
  if (value.status === 'stored') return '消息已保存；当前未触发模型回复。'
  if (value.status === 'duplicate') return '这条消息已存在，没有再次生成。'
  return `收件状态：${value.status}。请核对消息列表与轮次。`
}
function raw(value) {
  return value == null ? '没有保存的内容' : JSON.stringify(value, null, 2)
}

async function refreshState() {
  const request = ++stateRequest
  loading.value = true
  try {
    const result = await api('/api/chat-test/state')
    if (!active || request !== stateRequest) return
    state.value = result
    stateError.value = ''
    if (result.scene?.startsWith('private:')) form.value.uid = result.scene.slice('private:'.length)
  } catch (error) {
    if (active && request === stateRequest) stateError.value = error.message
  } finally {
    if (active && request === stateRequest) loading.value = false
  }
}

async function refreshDetail(focus = false) {
  if (!selectedTurnId.value) return
  const id = selectedTurnId.value
  const request = ++detailRequest
  detailLoading.value = true
  try {
    const result = await api(`/api/chat-test/turns/${encodeURIComponent(id)}`)
    if (!active || request !== detailRequest || id !== selectedTurnId.value) return
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

async function changed() {
  await refreshState()
  if (selectedTurnId.value) await refreshDetail()
}
function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return
  socketError.value = ''
  socketState.value = 'connecting'
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/chat-test/events`)
  socket = connection
  connection.onopen = () => {
    if (!active || socket !== connection) return
    socketState.value = 'connected'
    changed()
  }
  connection.onmessage = event => {
    if (!active || socket !== connection) return
    try {
      const notice = JSON.parse(event.data)
      if (notice?.type !== 'changed') throw new Error('未知的变化通知')
      changed()
    } catch (error) {
      socketError.value = `变化通知无法读取：${error.message}`
      connection.close()
    }
  }
  connection.onerror = () => {
    if (active && socket === connection) socketError.value = '实时连接出错；不会自动重连。'
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

async function submit() {
  attempted.value = true
  if (!canSubmit.value) return
  submitting.value = true
  submitError.value = ''
  receipt.value = null
  try {
    const result = await api('/api/chat-test/messages', {
      method: 'POST',
      body: JSON.stringify({
        uid: form.value.uid, nickname: form.value.nickname, text: form.value.text,
        mention_bot: isPrivate.value ? false : form.value.mention_bot,
        reply_to: form.value.reply_to || null
      })
    })
    receipt.value = result
    if (['queued', 'stored'].includes(result.status)) {
      form.value.text = ''
      form.value.reply_to = null
      attempted.value = false
    }
    await refreshState()
  } catch (error) {
    submitError.value = error.status >= 400 && error.status < 500
      ? `消息未被接受：${error.message}。不会自动重发。`
      : `提交结果未确认，不会自动重发。请先核对消息列表：${error.message}`
  } finally {
    submitting.value = false
  }
}

function selectTurn(turn) {
  if (selectedTurnId.value === turn.id) {
    selectedTurnId.value = null
    detail.value = null
    detailError.value = ''
    ++detailRequest
    return
  }
  selectedTurnId.value = turn.id
  detail.value = null
  detailError.value = ''
  refreshDetail(true)
}

onMounted(() => {
  refreshState()
  connect()
})
onBeforeUnmount(() => {
  active = false
  ++stateRequest
  ++detailRequest
  socket?.close()
})
</script>

<template>
  <div class="page-stack chat-test">
    <header class="page-intro">
      <div>
        <p class="eyebrow">单场景 · 隔离运行</p>
        <h1>对话测试</h1>
        <p class="muted">虚拟身份只进入本实例测试库；表达为模拟记录，不会发送到 QQ。模型请求会真实调用配置的服务，可能产生费用；未配置价格不显示零费用。</p>
      </div>
      <div class="intro-actions">
        <v-chip variant="tonal" :color="statusDisplay.color">
          {{ statusDisplay.text }}
        </v-chip>
        <v-btn variant="outlined" :loading="loading" @click="refreshState">读取最新记录</v-btn>
      </div>
    </header>

    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" title="读取测试状态失败">{{ stateError }}</v-alert>
    <v-alert v-if="state?.error" type="error" variant="tonal" role="alert" title="测试服务报告错误">{{ state.error }}</v-alert>
    <v-alert v-if="socketState==='disconnected' || socketError" type="warning" variant="tonal" role="alert">
      {{ socketError || '实时连接已断开；不会自动重连或重发消息。' }}
      <div class="mt-2"><v-btn variant="outlined" @click="reconnect">重新连接实时更新</v-btn></div>
    </v-alert>

    <div v-if="loading && !state" class="surface empty-state" role="status">正在读取隔离场景的实际记录…</div>

    <section v-if="state" class="binding surface" aria-label="当前隔离绑定">
      <div><span class="field-label">场景</span><strong class="entity-id">{{ state.scene }}</strong></div>
      <div><span class="field-label">角色</span><strong>{{ state.persona?.name }} <small class="muted">{{ state.persona?.id }}</small></strong></div>
      <div><span class="field-label">大脑 / 表达器</span><strong class="binding-model">{{ state.models?.mind }} / {{ state.models?.voice }}</strong></div>
      <div><span class="field-label">方式</span><strong>{{ state.voice_mode==='direct'?'直接表达':'表达器组织台词' }} · 模拟出口</strong></div>
    </section>

    <div class="work-grid">
      <section class="surface composer" aria-labelledby="composer-title">
        <div class="section-heading"><h2 id="composer-title">虚拟发言</h2><span class="muted">不会发到 QQ</span></div>
        <p class="muted">填写测试身份和原话。收件回执只说明消息已入库或入队，不代表模型已完成。</p>
        <form @submit.prevent="submit" novalidate>
          <v-text-field v-model="form.uid" label="虚拟 QQ 号" inputmode="numeric"
            :readonly="isPrivate" :disabled="!state" :error-messages="attempted ? uidError : ''" />
          <p v-if="isPrivate" class="field-hint">私聊场景固定为配置中的对方 QQ。</p>
          <v-text-field v-model="form.nickname" label="虚拟昵称" :disabled="!state"
            :error-messages="attempted ? nicknameError : ''" />
          <v-textarea v-model="form.text" label="消息正文" rows="5" auto-grow :disabled="!state"
            :error-messages="attempted ? textError : ''" />
          <v-checkbox v-if="!isPrivate" v-model="form.mention_bot" label="在消息中 @ Bot" hide-details />
          <v-select v-model="form.reply_to" label="引用本测试消息（可选）"
            :items="replyOptions" clearable :disabled="!state || !replyOptions.length"
            hint="可引用本实例的虚拟入站；模拟 Bot 表达暂不支持引用" persistent-hint />
          <v-btn type="submit" color="primary" block :loading="submitting" :disabled="!state?.running">
            提交测试消息
          </v-btn>
        </form>
        <v-alert v-if="submitError" type="error" variant="tonal" role="alert" class="mt-4">{{ submitError }}</v-alert>
        <v-alert v-if="receipt" :type="receipt.status==='error'?'error':'info'" variant="tonal" class="mt-4" role="status">
          {{ receiptLabel(receipt) }}
          <p v-if="receipt.error" class="receipt-error">{{ receipt.error }}</p>
          <p v-if="receipt.wake_channel" class="receipt-detail">唤醒通道：{{ receipt.wake_channel }}</p>
        </v-alert>
      </section>

      <div class="records">
        <section class="surface" aria-labelledby="messages-title">
          <div class="section-heading"><h2 id="messages-title">最近消息</h2><span class="muted">最多 50 条 · 完整原文</span></div>
          <div v-if="!messages.length" class="empty-state">还没有测试消息。先在左侧提交一条虚拟发言。</div>
          <ol v-else class="message-list">
            <li v-for="message in messages" :key="message.seq" class="message-item" :class="{'own-message':message.is_self}">
              <div class="message-meta">
                <strong>{{ message.is_self ? 'Bot · 模拟' : (message.sender?.nickname || '虚拟发言者') }}</strong>
                <span v-if="!message.is_self" class="entity-id">QQ {{ message.sender?.uid }}</span>
                <span>{{ localTime(message.time) }}</span>
              </div>
              <div class="message-text">{{ message.rendered }}</div>
              <div class="message-foot">
                <span>{{ statusLabel(message.send_status) }}</span>
              </div>
            </li>
          </ol>
        </section>

        <section class="surface" aria-labelledby="turns-title">
          <div class="section-heading"><h2 id="turns-title">对话轮次</h2><span class="muted">最近 20 轮</span></div>
          <p v-if="activeTurns.length" class="working-note" role="status">{{ activeTurns.length }} 轮正在执行或等待；模型请求开始与结束后会更新。</p>
          <div v-if="!turns.length" class="empty-state">尚无轮次。已保存的普通消息不一定会唤醒大脑。</div>
          <ul v-else class="turn-list">
            <li v-for="turn in turns" :key="turn.id" class="turn-item">
              <div class="turn-summary">
                <div><strong>{{ turnLabel(turn.status) }}</strong><span class="muted">{{ localTime(turn.started) }}</span></div>
                <v-btn variant="text" :aria-expanded="selectedTurnId===turn.id" @click="selectTurn(turn)">
                  {{ selectedTurnId===turn.id?'收起经过':'查看经过' }}
                </v-btn>
              </div>
              <p v-if="turn.error" class="turn-error">{{ turn.error }}</p>
            </li>
          </ul>
          <div v-if="selectedTurnId" class="turn-detail">
            <div class="detail-title">
              <h3 ref="detailHeading" tabindex="-1">轮次经过</h3>
              <v-btn variant="outlined" class="detail-refresh" :loading="detailLoading"
                @click="refreshDetail()">重新读取本轮</v-btn>
            </div>
            <p v-if="detailLoading" role="status">正在读取该轮的实际记录…</p>
            <v-alert v-if="detailError" type="error" variant="tonal" role="alert"
              :title="detail ? '读取失败 · 保留上次详情' : '读取轮次失败'">{{ detailError }}</v-alert>
            <template v-if="detail">
              <p>状态：{{ turnLabel(detail.turn.status) }} · 开始 {{ localTime(detail.turn.started) }} · 结束 {{ localTime(detail.turn.ended) }}</p>
              <div class="timing-facts">
                <p>首条表达：<template v-if="detail.turn.first_expression_at != null">{{ expressionDeliveryLabel(detail.turn.first_expression_delivery) }} · {{ localTime(detail.turn.first_expression_at) }}</template><template v-else>未记录</template></p>
                <p>轮开始至首条表达：{{ detail.turn.turn_to_first_expression_seconds == null ? '未知' : `${detail.turn.turn_to_first_expression_seconds} 秒` }}</p>
                <p>唤醒机会至首条表达：{{ detail.turn.wake_to_first_expression_seconds == null ? '未知' : `${detail.turn.wake_to_first_expression_seconds} 秒` }}</p>
              </div>
              <p v-if="detail.turn.error" class="turn-error">{{ detail.turn.error }}</p>
              <p v-if="!detailCalls.length" class="muted">这轮尚未保存模型请求。</p>
              <article v-for="call in detailCalls" :key="call.id" class="call-card">
                <div class="call-heading"><h4>{{ ({mind:'大脑',voice:'表达器',recap:'回想',vision:'视觉'})[call.role] || call.role }}</h4>
                  <span>{{ localTime(call.started) }} · {{ call.ended==null?'请求中':'已结束' }}</span></div>
                <p v-if="call.error" class="turn-error">{{ call.error }}</p>
                <p v-if="call.cost == null" class="muted">按配置估算费用未知；不计为 0。</p>
                <p v-else class="cost-fact">按调用时配置估算：<strong>{{ call.cost.currency }} {{ call.cost.amount }}</strong> <span class="muted">· 非供应商账单</span></p>
                <p v-if="call.usage == null" class="muted">提供方用量未知；不按 0 费用显示。</p>
                <p v-else class="usage">提供方返回用量：<code>{{ raw(call.usage) }}</code></p>
                <section v-if="call.tools.length" class="call-tools" aria-label="本次原生工具调用与已保存结果">
                  <h5>原生工具调用与已保存结果</h5>
                  <div v-for="tool in call.tools" :key="tool.id" class="tool-entry">
                    <strong class="tool-name">{{ tool.name }}</strong>
                    <details class="tool-text"><summary>查看参数原文</summary><pre>{{ tool.arguments }}</pre></details>
                    <p v-if="!tool.hasDirectAssociation" class="muted">没有直接关联，无法确认本次调用的工具结果。</p>
                    <p v-else-if="!tool.result" class="muted">尚无已保存结果；不能据此判断是否执行。</p>
                    <details v-else class="tool-text"><summary>已保存工具结果 · 不代表执行完成</summary><pre>{{ tool.result.content }}</pre></details>
                  </div>
                </section>
                <details><summary>查看原始请求与响应</summary>
                  <h5>请求</h5><pre>{{ raw(call.request) }}</pre>
                  <h5>响应</h5><pre>{{ raw(call.response) }}</pre>
                </details>
              </article>
            </template>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-test{max-width:1480px;margin-inline:auto}
.page-intro{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;min-width:0}
.page-intro h1{font-size:28px;line-height:1.25;margin:4px 0 12px}
.page-intro p{max-width:760px;margin-bottom:0}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0}
.intro-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;flex:none}
.binding{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px}
.binding>div{display:grid;gap:4px;min-width:0}
.binding strong{font-size:14px;overflow-wrap:anywhere}
.binding-model{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.field-label{font-size:12px;color:var(--muted)}
.work-grid{display:grid;grid-template-columns:minmax(280px,360px) minmax(0,1fr);gap:20px;align-items:start;min-width:0}
.records{display:grid;gap:20px;min-width:0}
.composer,.records .surface{min-width:0}
.section-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.section-heading h2{font-size:18px;line-height:1.4;margin:0}
.section-heading span{font-size:12px}
.composer>p{font-size:13px;margin-bottom:20px}
.composer form{display:grid;gap:12px}
.field-hint{font-size:12px;color:var(--muted);margin:-8px 0 0}
.receipt-error,.receipt-detail{margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.message-list,.turn-list{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.message-item{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.own-message{background:var(--bot-avatar-bg)}
.message-meta,.message-foot{display:flex;align-items:center;gap:8px 16px;flex-wrap:wrap;min-width:0;color:var(--muted);font-size:12px}
.message-meta strong{color:var(--ink);font-size:14px}
.message-text{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;max-height:400px;overflow:auto;line-height:1.75}
.message-foot{border-top:1px solid var(--line);padding-top:8px}
.turn-item{border-bottom:1px solid var(--line);padding:0 0 10px;min-width:0}
.turn-summary,.call-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.turn-summary>div{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;min-width:0}
.turn-summary .v-btn{min-height:44px}
.turn-error{color:var(--error-text);white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}
.working-note{border-left:3px solid var(--primary);padding:8px 12px;background:var(--selected-bg);margin-bottom:18px}
.turn-detail{border:1px solid var(--line);border-radius:10px;background:var(--list-heading-bg);padding:16px;margin-top:20px;min-width:0}
.turn-detail h3{font-size:16px;margin-bottom:12px}
.detail-title{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.detail-title h3{margin:0}
.detail-refresh{min-height:44px}
.timing-facts{border-left:3px solid var(--primary);padding:8px 12px;margin:12px 0;background:var(--selected-bg)}
.timing-facts p{margin:4px 0}
.turn-detail p{overflow-wrap:anywhere}
.call-card{border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:14px;margin-top:12px;min-width:0}
.call-heading h4{font-size:14px;margin:0}
.call-heading span{font-size:12px;color:var(--muted)}
.usage code{white-space:pre-wrap;overflow-wrap:anywhere}
.cost-fact strong{overflow-wrap:anywhere}
.call-tools{border-top:1px solid var(--line);margin-top:14px;padding-top:12px;min-width:0}
.call-tools h5{font-size:13px;margin:0 0 10px}
.tool-entry{border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:10px;min-width:0}
.tool-name{display:block;overflow-wrap:anywhere}
.tool-entry p{margin:10px 0 0}
.tool-text{min-width:0}
.tool-text pre{max-height:260px}
.call-card details{min-width:0}
.call-card summary{cursor:pointer;color:var(--primary);font-weight:600;min-height:44px;box-sizing:border-box;padding:10px 0}
.call-card summary:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.call-card h5{font-size:12px;margin:12px 0 6px}
.call-card pre{max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:var(--code-bg);padding:12px;border-radius:8px;font-size:12px;line-height:1.6}
@media(max-width:1050px){.binding{grid-template-columns:repeat(2,minmax(0,1fr))}.work-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:600px){.page-intro{display:grid;gap:16px}.page-intro h1{font-size:24px}.intro-actions{width:100%}.binding{grid-template-columns:minmax(0,1fr);gap:12px}.message-item{padding:12px}.turn-detail{padding:12px}}
</style>
