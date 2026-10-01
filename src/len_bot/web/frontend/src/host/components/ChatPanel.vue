<script setup>
// A test conversation: type as a made-up group member, see the Bot's simulated
// replies, and open any reply round to see what happened. Nothing goes to QQ.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { messageLabel, turnLabel, turnFailed } from '../labels.js'
import { formatTime } from '../time.js'
import ErrorNote from './ErrorNote.vue'
import LiveStatus from './LiveStatus.vue'
import TurnDetail from './TurnDetail.vue'
import DevOnly from './DevOnly.vue'

const props = defineProps({ apiBase: { type: String, required: true } })
const state = useResource(() => api(`${props.apiBase}/state`))
const view = computed(() => state.data.value)
const isPrivate = computed(() => view.value?.scene?.startsWith('private:') || false)
const messages = computed(() => [...(view.value?.messages || [])].sort((a, b) => a.seq - b.seq))
const turns = computed(() => [...(view.value?.turns || [])].sort((a, b) => b.started - a.started))
const thinking = computed(() => turns.value.some(turn => turn.ended == null))

const uid = ref('10001'), nickname = ref('测试群友'), text = ref(''), mention = ref(true), replyTo = ref(null)
const replies = computed(() => messages.value.filter(message => message.platform_message_id != null).map(message => ({
  title: message.text.replace(/\s+/g, ' ').slice(0, 60), value: message.platform_message_id })))
const send = useAction(), receipt = ref(null)
const ready = computed(() => view.value?.running && /^[1-9][0-9]*$/.test(isPrivate.value ? view.value.scene.slice(8) : uid.value)
  && nickname.value.trim() && text.value.trim())
async function submit() {
  const result = await send.run(() => api(`${props.apiBase}/messages`, { method: 'POST', body: JSON.stringify({
    uid: isPrivate.value ? view.value.scene.slice('private:'.length) : uid.value, nickname: nickname.value, text: text.value,
    mention_bot: isPrivate.value ? false : mention.value, reply_to: replyTo.value || null }) }))
  if (!result) return
  receipt.value = result
  if (['queued', 'stored'].includes(result.status)) { text.value = ''; replyTo.value = null }
  state.reload()
}
function keydown(event) {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && ready.value) submit()
}

const openTurn = ref(null)
const detail = useResource(() => api(`${props.apiBase}/turns/${encodeURIComponent(openTurn.value)}`), { immediate: false })
function toggle(turn) {
  openTurn.value = openTurn.value === turn.id ? null : turn.id
  if (openTurn.value) detail.reload()
}

// Change notices from the test's own event stream.
const live = ref('connecting')
let socket = null
function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}${props.apiBase}/events`)
  socket = connection
  live.value = 'connecting'
  connection.onopen = () => { if (socket === connection) { live.value = 'connected'; state.reload() } }
  connection.onmessage = () => {
    if (socket !== connection) return
    state.reload()
    if (openTurn.value) detail.reload()
  }
  connection.onclose = () => { if (socket === connection) live.value = 'disconnected' }
}
function reconnect() { socket?.close(); connect() }
onMounted(connect)
onBeforeUnmount(() => { const connection = socket; socket = null; connection?.close() })
const receiptText = { stored: '已收到，这条消息不会叫醒 Bot', duplicate: '这条消息已经发过了' }
</script>

<template>
  <div class="chat-panel">
    <ErrorNote v-if="state.error.value" title="读取对话失败" :error="state.error.value" />
    <ErrorNote v-if="view?.error" title="测试运行出错了" :error="view.error" />
    <div v-if="view" class="layout">
      <section class="surface conversation">
        <div class="head">
          <span><strong>{{ view.persona?.name }}</strong><span class="muted"> · {{ view.running ? '进行中' : '已停止' }}</span></span>
          <LiveStatus :status="live" @reconnect="reconnect" />
        </div>
        <ol class="messages">
          <li v-if="!messages.length" class="empty muted">发一条消息开始聊吧</li>
          <li v-for="message in messages" :key="message.seq" :class="{ self: message.is_self }">
            <span class="who">{{ message.is_self ? view.persona?.name : (message.sender?.nickname || `QQ ${message.sender?.uid}`) }}
              <span class="muted">{{ formatTime(message.time, view.timezone, { date: false }) }}</span></span>
            <div class="bubble">{{ message.text }}</div>
            <span v-if="message.is_self && message.send_status !== 'simulated'" class="status">{{ messageLabel(message.send_status) }}</span>
          </li>
          <li v-if="thinking" class="self"><div class="bubble typing">正在想…</div></li>
        </ol>
        <form v-if="view.running" class="composer" @submit.prevent="submit">
          <div v-if="!isPrivate" class="identity">
            <v-text-field v-model="nickname" label="昵称" density="compact" hide-details />
            <v-text-field v-model="uid" label="QQ" inputmode="numeric" density="compact" hide-details />
            <v-checkbox v-model="mention" label="@ Bot" density="compact" hide-details />
          </div>
          <v-select v-if="replies.length" v-model="replyTo" :items="replies" label="引用一条消息（可不选）" density="compact" clearable hide-details />
          <div class="send-row">
            <v-textarea v-model="text" label="说点什么" rows="1" auto-grow hide-details density="comfortable" @keydown="keydown" />
            <v-btn type="submit" color="primary" :loading="send.busy.value" :disabled="!ready">发送</v-btn>
          </div>
          <ErrorNote v-if="send.error.value" title="没有发出去" :error="send.error.value" />
          <p v-if="receipt && receiptText[receipt.status]" class="muted">{{ receiptText[receipt.status] }}</p>
          <ErrorNote v-if="receipt?.error" title="处理这条消息出错了" :error="receipt.error" />
        </form>
      </section>
      <section class="surface rounds">
        <h2>回复经过</h2>
        <p v-if="!turns.length" class="muted">Bot 回复后，这里能看到它每一轮是怎么想的。</p>
        <ul>
          <li v-for="turn in turns" :key="turn.id">
            <button type="button" :class="{ failed: turnFailed(turn.status) }" @click="toggle(turn)">
              <span>{{ formatTime(turn.started, view.timezone, { date: false }) }}</span><strong>{{ turnLabel(turn.status) }}</strong></button>
            <div v-if="openTurn === turn.id" class="turn">
              <ErrorNote v-if="detail.error.value" title="读取这一轮失败" :error="detail.error.value" />
              <TurnDetail v-if="detail.data.value?.turn?.id === turn.id" :detail="detail.data.value" :timezone="view.timezone" />
            </div>
          </li>
        </ul>
        <DevOnly label="测试绑定"><pre>{{ JSON.stringify({ scene: view.scene, models: view.models, voice_mode: view.voice_mode }, null, 2) }}</pre></DevOnly>
      </section>
    </div>
  </div>
</template>

<style scoped>
.chat-panel{display:grid;gap:16px;min-width:0}
.layout{display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,2fr);gap:16px;align-items:start}
.conversation{display:grid;gap:12px;padding:16px}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.messages{list-style:none;margin:0;padding:4px;display:grid;gap:12px;max-height:60vh;overflow:auto}
.messages li{display:grid;gap:4px;justify-items:start;max-width:85%}
.messages li.self{justify-self:end;justify-items:end}
.empty{justify-self:center}
.who{font-size:12px;color:var(--muted)}
.bubble{background:var(--list-heading-bg);border-radius:12px;padding:8px 12px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}
.self .bubble{background:var(--selected-bg)}
.typing{color:var(--muted)}
.status{font-size:12px;color:var(--warning-text)}
.composer{display:grid;gap:8px;border-top:1px solid var(--line);padding-top:12px}
.identity{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:center}
.send-row{display:flex;gap:8px;align-items:flex-end}
.send-row>:first-child{flex:1}
.rounds{display:grid;gap:8px}
.rounds ul{list-style:none;margin:0;padding:0;display:grid}
.rounds li{border-bottom:1px solid var(--line)}
.rounds li:last-child{border-bottom:0}
.rounds button{display:flex;gap:12px;width:100%;padding:8px 0;background:none;border:0;cursor:pointer;color:inherit;font:inherit;text-align:left}
.rounds button.failed strong{color:var(--error-text)}
.turn{padding-bottom:10px}
@media(max-width:900px){.layout{grid-template-columns:1fr}.messages{max-height:none}}
@media(max-width:600px){.identity{grid-template-columns:1fr 1fr}}
</style>
