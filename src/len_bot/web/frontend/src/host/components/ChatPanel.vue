<script setup>
// A test conversation: type as a made-up group member, see the Bot's simulated
// replies, and open any reply round to see what happened. Nothing goes to 平台账号.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { formatTime } from '../time.js'
import Panel from '../ui/Panel.vue'
import ObjectList from '../ui/ObjectList.vue'
import ObjectRow from '../ui/ObjectRow.vue'
import StatusBadge from '../ui/StatusBadge.vue'
import ErrorNote from '../ui/ErrorNote.vue'
import LiveStatus from '../ui/LiveStatus.vue'
import TurnDetail from './TurnDetail.vue'
import DevOnly from '../ui/DevOnly.vue'

const props = defineProps({ apiBase: { type: String, required: true } })
const state = useResource(() => api(`${props.apiBase}/state`))
const view = computed(() => state.data.value)
const isPrivate = computed(() => view.value?.scene?.startsWith('onebot:private:') || false)
const messages = computed(() => [...(view.value?.messages || [])].sort((a, b) => a.seq - b.seq))
const turns = computed(() => [...(view.value?.turns || [])].sort((a, b) => b.started - a.started))
const thinking = computed(() => turns.value.some(turn => turn.ended == null))

const uid = ref('10001'), nickname = ref('测试群友'), text = ref(''), mention = ref(true), replyTo = ref(null)
const replies = computed(() => messages.value.filter(message => message.platform_message_id != null).map(message => ({
  title: message.text.replace(/\s+/g, ' ').slice(0, 60), value: message.platform_message_id })))
const send = useAction(), receipt = ref(null)
const ready = computed(() => view.value?.running && /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(isPrivate.value ? view.value.scene.slice(8) : uid.value)
  && nickname.value.trim() && text.value.trim())
async function submit() {
  const result = await send.run(() => api(`${props.apiBase}/messages`, { method: 'POST', body: JSON.stringify({
    uid: isPrivate.value ? view.value.scene.slice('onebot:private:'.length) : uid.value, nickname: nickname.value, text: text.value,
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
    <ErrorNote v-if="state.error.value" title="读取对话失败" :error="state.error.value" @retry="state.reload()" />
    <ErrorNote v-if="view?.error" title="测试运行出错了" :error="view.error" />
    <div v-if="view" class="layout">
      <Panel>
        <template #title><h2>{{ view.persona?.name }} <span class="muted small">· {{ view.running ? '进行中' : '已停止' }}</span></h2></template>
        <template #actions><LiveStatus :status="live" @reconnect="reconnect" /></template>
        <ol class="messages">
          <li v-if="!messages.length" class="empty muted">发一条消息开始聊吧</li>
          <li v-for="message in messages" :key="message.seq" :class="{ self: message.is_self }">
            <span class="who">{{ message.is_self ? view.persona?.name : (message.sender?.nickname || `平台账号 ${message.sender?.uid}`) }}
              · {{ formatTime(message.time, view.timezone, { date: false }) }}</span>
            <div class="bubble">{{ message.text }}</div>
            <StatusBadge v-if="message.is_self && message.send_status !== 'simulated'" dot kind="message" :value="message.send_status" />
          </li>
          <li v-if="thinking" class="self"><div class="bubble typing">正在想…</div></li>
        </ol>
        <form v-if="view.running" class="composer" @submit.prevent="submit">
          <div v-if="!isPrivate" class="identity">
            <v-text-field v-model="nickname" label="昵称" density="compact" hide-details />
            <v-text-field v-model="uid" label="平台账号"  density="compact" hide-details />
            <v-checkbox v-model="mention" label="@ Bot" density="compact" hide-details />
          </div>
          <v-select v-if="replies.length" v-model="replyTo" :items="replies" label="引用一条消息（可不选）" density="compact" clearable hide-details />
          <div class="send-row">
            <v-textarea v-model="text" label="说点什么" rows="1" auto-grow hide-details density="comfortable" @keydown="keydown" />
            <v-btn type="submit" color="primary" :loading="send.busy.value" :disabled="!ready">发送</v-btn>
          </div>
          <ErrorNote v-if="send.error.value" title="没有发出去" :error="send.error.value" />
          <p v-if="receipt && receiptText[receipt.status]" class="muted small">{{ receiptText[receipt.status] }}</p>
          <ErrorNote v-if="receipt?.error" title="处理这条消息出错了" :error="receipt.error" />
        </form>
      </Panel>
      <Panel title="回复经过" flush>
        <p v-if="!turns.length" class="muted small hint">Bot 回复后，这里能看到它每一轮是怎么想的。</p>
        <ObjectList v-else divided class="turns">
          <template v-for="turn in turns" :key="turn.id">
            <ObjectRow clickable :active="openTurn === turn.id" :title="formatTime(turn.started, view.timezone, { date: false })" @click="toggle(turn)">
              <template #meta><StatusBadge dot kind="turn" :value="turn.status" /></template>
            </ObjectRow>
            <li v-if="openTurn === turn.id" class="turn">
              <ErrorNote v-if="detail.error.value" title="读取这一轮失败" :error="detail.error.value" @retry="detail.reload()" />
              <TurnDetail v-if="detail.data.value?.turn?.id === turn.id" :detail="detail.data.value" :timezone="view.timezone" />
            </li>
          </template>
        </ObjectList>
        <DevOnly label="测试绑定" :json="{ scene: view.scene, models: view.models, voice_mode: view.voice_mode }" class="hint" />
      </Panel>
    </div>
  </div>
</template>

<style scoped>
.chat-panel{display:grid;gap:var(--sp-4);min-width:0}
.layout{display:grid;grid-template-columns:minmax(0,3fr) minmax(260px,2fr);gap:var(--sp-4);align-items:start}
.messages{list-style:none;margin:0;padding:var(--sp-1);display:grid;gap:var(--sp-3);max-height:60vh;overflow:auto}
.messages li{display:grid;gap:var(--sp-1);justify-items:start;max-width:85%}
.messages li.self{justify-self:end;justify-items:end}
.empty{justify-self:center}
.who{font-size:var(--fs-xs);color:var(--muted)}
.bubble{background:var(--hover);border-radius:var(--radius-lg);padding:var(--sp-2) var(--sp-3);white-space:pre-wrap;overflow-wrap:anywhere}
.self .bubble{background:var(--selected)}
.typing{color:var(--muted)}
.composer{display:grid;gap:var(--sp-2);border-top:1px solid var(--line);padding-top:var(--sp-3)}
.composer p{margin:0}
.identity{display:grid;grid-template-columns:1fr 1fr auto;gap:var(--sp-2);align-items:center}
.send-row{display:flex;gap:var(--sp-2);align-items:flex-end}
.send-row>:first-child{flex:1}
.hint{margin:0;padding:0 var(--sp-4) var(--sp-4)}
.turns{padding:0 var(--sp-2) var(--sp-2)}
.turn{padding:var(--sp-2) var(--sp-3) var(--sp-3)}
@media(max-width:900px){.layout{grid-template-columns:1fr}.messages{max-height:none}}
@media(max-width:600px){.identity{grid-template-columns:1fr 1fr}}
</style>
