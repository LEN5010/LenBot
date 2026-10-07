<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { mdiAt, mdiClose, mdiPencilOutline, mdiPlus, mdiReplyOutline, mdiTimelineTextOutline } from '@mdi/js'
import { api, sceneName } from '../../api.js'
import { useAction, useResource } from '../../composables/useResource.js'
import { avatarTints } from '../../styles/theme.js'
import { formatTime } from '../time.js'
import ObjectList from '../ui/ObjectList.vue'
import ObjectRow from '../ui/ObjectRow.vue'
import StatusBadge from '../ui/StatusBadge.vue'
import ErrorNote from '../ui/ErrorNote.vue'
import FormDialog from '../ui/FormDialog.vue'
import LiveStatus from '../ui/LiveStatus.vue'
import TurnDetail from './TurnDetail.vue'
import DevOnly from '../ui/DevOnly.vue'

const props = defineProps({ apiBase: { type: String, required: true } })
const state = useResource(() => api(`${props.apiBase}/state`))
const view = computed(() => state.data.value)
const isPrivate = computed(() => view.value?.scene?.split(':')[1] === 'private')
const messages = computed(() => [...(view.value?.messages || [])].sort((a, b) => a.seq - b.seq))
const turns = computed(() => [...(view.value?.turns || [])].sort((a, b) => b.started - a.started))
const thinking = computed(() => turns.value.some(turn => turn.ended == null))
const botName = computed(() => view.value?.persona?.name || 'Bot')

// Simulated members, kept per scene in this browser.
const roles = [{ title: '成员', value: 'member' }, { title: '管理员', value: 'admin' }, { title: '群主', value: 'owner' }]
const roleLabel = { owner: '群主', admin: '管理员' }
const members = ref([]), current = ref('')
const storageKey = scene => `lenbot.chatTest.members.${scene}`
watch(() => view.value?.scene, scene => {
  if (!scene) return
  if (isPrivate.value) {
    members.value = [{ uid: `${scene.split(':')[0]}:${scene.split(':').slice(2).join(':')}`, nickname: '对方', role: 'member' }]
  } else {
    let saved = null
    try { saved = JSON.parse(localStorage.getItem(storageKey(scene)) || 'null') } catch { saved = null }
    members.value = Array.isArray(saved) && saved.length ? saved
      : [{ uid: 'onebot:10001', nickname: '阿青', role: 'member' }, { uid: 'onebot:10002', nickname: '小周', role: 'member' }]
  }
  current.value = members.value[0].uid
}, { immediate: true })
watch(members, value => { if (view.value && !isPrivate.value) localStorage.setItem(storageKey(view.value.scene), JSON.stringify(value)) }, { deep: true })
const speaker = computed(() => members.value.find(member => member.uid === current.value) || members.value[0])

const editing = ref(null)
function addMember() {
  const used = new Set(members.value.map(member => member.uid))
  let number = 10001
  while (used.has(`onebot:${number}`)) number++
  editing.value = { uid: `onebot:${number}`, nickname: `群友${members.value.length + 1}`, role: 'member', adding: true }
}
const editMember = member => { editing.value = { ...member, adding: false } }
function saveMember() {
  const { adding, ...member } = editing.value
  member.nickname = member.nickname.trim()
  members.value = adding ? [...members.value, member] : members.value.map(item => item.uid === member.uid ? member : item)
  current.value = member.uid
  editing.value = null
}
function removeMember() {
  members.value = members.value.filter(item => item.uid !== editing.value.uid)
  if (current.value === editing.value.uid) current.value = members.value[0].uid
  editing.value = null
}

// Names and avatars.
function nameOf(uid) {
  if (uid === 'all') return '全体成员'
  if (uid === view.value?.bot_id) return botName.value
  return members.value.find(member => member.uid === uid)?.nickname
    || messages.value.find(message => message.sender?.uid === uid)?.sender.nickname || uid
}
const tint = uid => avatarTints[[...uid].reduce((sum, char) => sum + char.codePointAt(0), 0) % avatarTints.length]
const avatarFailed = ref(false)

// Message content from segments, as QQ shows it.
const byPlatformId = computed(() => Object.fromEntries(messages.value.filter(message => message.platform_message_id)
  .map(message => [message.platform_message_id, message])))
function excerpt(message) {
  return message.segments.map(segment => segment.type === 'text' ? segment.data.text
    : segment.type === 'mention' ? `@${nameOf(segment.data.user)} ` : segment.type === 'image' ? '[图片]' : '').join('').trim().slice(0, 60)
}
function quoted(message) {
  const id = message.segments.find(segment => segment.type === 'reply')?.data.id ?? message.reply_to
  return id ? { id, message: byPlatformId.value[id] || null } : null
}
function pieces(message) {
  let image = 0
  return message.segments.flatMap(segment => {
    if (segment.type === 'text') return [{ kind: 'text', text: segment.data.text }]
    if (segment.type === 'mention') return [{ kind: 'mention', text: `@${nameOf(segment.data.user)}` }]
    if (segment.type === 'image') {
      image++
      const stored = message.images?.find(item => item.image_index === image)
      return [stored ? { kind: 'image', src: `${props.apiBase}/messages/${message.seq}/images/${image}`, alt: stored.description || '图片' }
        : { kind: 'other', text: '[图片]' }]
    }
    if (segment.type === 'reply') return []
    return [{ kind: 'other', text: `[${segment.type}]` }]
  })
}
const list = ref(null)
function jump(message) {
  const element = list.value?.querySelector(`[data-seq="${message.seq}"]`)
  element?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  element?.classList.add('flash')
  setTimeout(() => element?.classList.remove('flash'), 1200)
}
watch(() => messages.value.length, async () => {
  await nextTick()
  if (list.value) list.value.scrollTop = list.value.scrollHeight
})

// Composer.
const text = ref(''), mentions = ref([]), replyTo = ref(null), input = ref(null)
const mentionMenu = ref(false)
const mentionChoices = computed(() => [
  { uid: view.value?.bot_id, title: botName.value },
  ...members.value.filter(member => member.uid !== speaker.value?.uid).map(member => ({ uid: member.uid, title: member.nickname })),
  { uid: 'all', title: '全体成员' },
].filter(item => item.uid && !mentions.value.includes(item.uid)))
function addMention(uid) {
  mentions.value = [...mentions.value, uid]
  mentionMenu.value = false
  input.value?.focus()
}
function typed(value) {
  if (!isPrivate.value && value.endsWith('@') && value.length > text.value.length) {
    text.value = value.slice(0, -1)
    mentionMenu.value = true
  } else text.value = value
}
function reply(message) {
  replyTo.value = message
  input.value?.focus()
}
const send = useAction(), receipt = ref(null)
const ready = computed(() => view.value?.running && speaker.value && text.value.trim())
async function submit() {
  if (!ready.value) return
  const result = await send.run(() => api(`${props.apiBase}/messages`, { method: 'POST', body: JSON.stringify({
    uid: speaker.value.uid, nickname: speaker.value.nickname, role: speaker.value.role, text: text.value,
    mentions: isPrivate.value ? [] : mentions.value, reply_to: replyTo.value?.platform_message_id || null }) }))
  if (!result) return
  receipt.value = result
  if (['queued', 'stored'].includes(result.status)) { text.value = ''; mentions.value = []; replyTo.value = null }
  state.reload()
}
function keydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}

// Turns beside the chat.
const showTurns = ref(true)
const openTurn = ref(null)
const detail = useResource(() => api(`${props.apiBase}/turns/${encodeURIComponent(openTurn.value)}`), { immediate: false })
function toggle(turn) {
  openTurn.value = openTurn.value === turn.id ? null : turn.id
  if (openTurn.value) detail.reload()
}
function showTurnOf(message) {
  const turn = turns.value.find(item => item.started <= message.time && (item.ended == null || message.time <= item.ended))
  if (!turn) return
  showTurns.value = true
  if (openTurn.value !== turn.id) toggle(turn)
}

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
const receiptText = { stored: '这条没有唤醒 Bot', duplicate: '这条消息已经发过了' }
</script>

<template>
  <div class="chat-panel">
    <ErrorNote v-if="state.error.value" title="读取对话失败" :error="state.error.value" @retry="state.reload()" />
    <ErrorNote v-if="view?.error" title="测试运行出错了" :error="view.error" />
    <div v-if="view" class="layout" :class="{ wide: showTurns }">
      <section class="window">
        <header class="window-head">
          <div class="title">
            <strong>{{ isPrivate ? botName : sceneName(view.scene) }}</strong>
            <span class="muted small">{{ isPrivate ? '私聊' : `${members.length + 1} 人` }}{{ view.running ? '' : ' · 已结束' }}</span>
          </div>
          <LiveStatus :status="live" @reconnect="reconnect" />
          <v-btn :icon="mdiTimelineTextOutline" variant="text" size="small" :color="showTurns ? 'primary' : undefined"
            aria-label="回复经过" title="回复经过" @click="showTurns = !showTurns" />
        </header>

        <ol ref="list" class="messages">
          <li v-if="!messages.length" class="empty muted">还没有消息</li>
          <li v-for="message in messages" :key="message.seq" :data-seq="message.seq" class="message" :class="{ mine: !message.is_self }">
            <span class="avatar">
              <img v-if="message.is_self && !avatarFailed" :src="`${apiBase}/avatar`" alt="" @error="avatarFailed = true" />
              <span v-else class="initial" :style="{ background: tint(message.sender.uid)[0], color: tint(message.sender.uid)[1] }">
                {{ [...(message.is_self ? botName : nameOf(message.sender.uid))][0] }}</span>
            </span>
            <div class="body">
              <div class="who">
                <span v-if="roleLabel[message.sender.role]" class="role" :class="message.sender.role">{{ roleLabel[message.sender.role] }}</span>
                <span>{{ message.is_self ? botName : message.sender.nickname || message.sender.uid }}</span>
                <span class="time">{{ formatTime(message.time, view.timezone, { date: false }) }}</span>
                <StatusBadge v-if="message.is_self && message.send_status !== 'simulated'" dot kind="message" :value="message.send_status" />
                <v-btn v-if="view.running && message.platform_message_id" size="x-small" variant="text" :prepend-icon="mdiReplyOutline"
                  class="reply-action" @click="reply(message)">回复</v-btn>
              </div>
              <div class="bubble" :class="{ clickable: message.is_self, bare: !quoted(message) && pieces(message).every(piece => piece.kind === 'image') }" @click="message.is_self && showTurnOf(message)">
                <button v-if="quoted(message)" type="button" class="quote" :disabled="!quoted(message).message"
                  @click.stop="quoted(message).message && jump(quoted(message).message)">
                  <template v-if="quoted(message).message"><strong>{{ quoted(message).message.is_self ? botName : nameOf(quoted(message).message.sender.uid) }}：</strong>{{ excerpt(quoted(message).message) }}</template>
                  <template v-else>引用的消息不在这里</template>
                </button>
                <template v-for="(piece, index) in pieces(message)" :key="index">
                  <span v-if="piece.kind === 'mention'" class="mention">{{ `${piece.text} ` }}</span>
                  <img v-else-if="piece.kind === 'image'" class="picture" :src="piece.src" :alt="piece.alt" :title="piece.alt" loading="lazy" />
                  <span v-else-if="piece.kind === 'other'" class="muted">{{ piece.text }}</span>
                  <span v-else>{{ piece.text }}</span>
                </template>
              </div>
            </div>
          </li>
          <li v-if="thinking" class="typing muted small">{{ botName }} 正在输入…</li>
        </ol>

        <form v-if="view.running" class="composer" @submit.prevent="submit">
          <div v-if="!isPrivate" class="member-bar" role="radiogroup" aria-label="以谁的身份发言">
            <button v-for="member in members" :key="member.uid" type="button" class="member" role="radio"
              :class="{ active: member.uid === speaker?.uid }" :aria-checked="member.uid === speaker?.uid" @click="current = member.uid">
              <span class="initial small-avatar" :style="{ background: tint(member.uid)[0], color: tint(member.uid)[1] }">{{ [...member.nickname][0] }}</span>
              {{ member.nickname }}<span v-if="roleLabel[member.role]" class="role" :class="member.role">{{ roleLabel[member.role] }}</span>
            </button>
            <v-btn v-if="speaker" :icon="mdiPencilOutline" variant="text" size="x-small" aria-label="编辑当前群友" @click="editMember(speaker)" />
            <v-btn :icon="mdiPlus" variant="text" size="x-small" aria-label="添加群友" @click="addMember" />
          </div>
          <div v-if="replyTo" class="reply-bar">
            <span><strong>回复 {{ replyTo.is_self ? botName : nameOf(replyTo.sender.uid) }}：</strong>{{ excerpt(replyTo) }}</span>
            <v-btn :icon="mdiClose" variant="text" size="x-small" aria-label="取消回复" @click="replyTo = null" />
          </div>
          <div v-if="mentions.length" class="mention-chips">
            <v-chip v-for="uid in mentions" :key="uid" size="small" closable color="primary" variant="tonal"
              @click:close="mentions = mentions.filter(item => item !== uid)">@{{ nameOf(uid) }}</v-chip>
          </div>
          <div class="send-row">
            <v-menu v-if="!isPrivate" v-model="mentionMenu" location="top start">
              <template #activator="{ props: menu }"><v-btn v-bind="menu" :icon="mdiAt" variant="text" aria-label="@ 群友" /></template>
              <v-list density="compact">
                <v-list-item v-for="item in mentionChoices" :key="item.uid" :title="item.title" @click="addMention(item.uid)" />
              </v-list>
            </v-menu>
            <v-textarea ref="input" :model-value="text" :placeholder="speaker ? `以 ${speaker.nickname} 的身份发言` : ''" rows="1" auto-grow max-rows="6"
              hide-details density="comfortable" @update:model-value="typed" @keydown="keydown" />
            <v-btn type="submit" color="primary" :loading="send.busy.value" :disabled="!ready">发送</v-btn>
          </div>
          <ErrorNote v-if="send.error.value" title="没有发出去" :error="send.error.value" />
          <p v-if="receipt && receiptText[receipt.status]" class="muted small">{{ receiptText[receipt.status] }}</p>
          <ErrorNote v-if="receipt?.error" title="处理这条消息出错了" :error="receipt.error" />
        </form>
      </section>

      <section v-if="showTurns" class="turns-panel">
        <h3>回复经过</h3>
        <p v-if="!turns.length" class="muted small">还没有回复</p>
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
        <DevOnly label="测试绑定" :json="{ scene: view.scene, models: view.models, voice_mode: view.voice_mode }" />
      </section>
    </div>

    <FormDialog :model-value="editing !== null" :title="editing?.adding ? '添加群友' : '编辑群友'" @update:model-value="value => { if (!value) editing = null }">
      <template v-if="editing">
        <v-text-field v-model="editing.nickname" label="昵称" />
        <v-text-field :model-value="editing.uid" label="账号" readonly />
        <v-select v-model="editing.role" :items="roles" label="身份" />
      </template>
      <template v-if="editing && !editing.adding && members.length > 1" #danger>
        <v-btn variant="text" color="error" @click="removeMember">删除</v-btn>
      </template>
      <template #actions><v-btn color="primary" :disabled="!editing?.nickname.trim()" @click="saveMember">保存</v-btn></template>
    </FormDialog>
  </div>
</template>

<style scoped>
.chat-panel{display:grid;gap:var(--sp-4);min-width:0}
.layout{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--sp-4);align-items:start}
.layout.wide{grid-template-columns:minmax(0,3fr) minmax(260px,2fr)}
.window{display:grid;grid-template-rows:auto minmax(0,1fr) auto;border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);height:min(78vh,820px);min-height:420px;overflow:hidden}
.window-head{display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--line)}
.title{display:grid;flex:1;min-width:0}
.title strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.messages{list-style:none;margin:0;padding:var(--sp-4);display:grid;gap:var(--sp-3);align-content:start;overflow:auto;background:var(--hover)}
.empty{justify-self:center}
.message{display:flex;gap:var(--sp-2);align-items:flex-start;max-width:80%;border-radius:var(--radius);transition:background .3s}
.message.mine{flex-direction:row-reverse;justify-self:end}
.message.flash{background:var(--selected)}
.avatar{flex:none;width:36px;height:36px}
.avatar img,.initial{width:36px;height:36px;border-radius:50%;object-fit:cover;display:grid;place-items:center;font-weight:700}
.body{display:grid;gap:4px;min-width:0}
.mine .body{justify-items:end}
.who{display:flex;align-items:center;gap:var(--sp-1);font-size:var(--fs-xs);color:var(--muted);min-height:22px}
.mine .who{flex-direction:row-reverse}
.time{opacity:.7}
.role{font-size:11px;line-height:16px;padding:0 4px;border-radius:4px;background:var(--track);color:var(--muted)}
.role.owner{background:#fdf0d5;color:#9a6a00}
.role.admin{background:#e3f2ea;color:#2a7457}
.bubble{background:var(--surface);border-radius:var(--radius-lg);padding:var(--sp-2) var(--sp-3);white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%}
.bubble.clickable{cursor:pointer}
.mine .bubble{background:var(--selected)}
.bubble.bare{background:none;padding:0}
.quote{display:block;width:100%;text-align:left;font:inherit;font-size:var(--fs-xs);color:var(--muted);background:rgb(0 0 0 / 4%);border:0;border-left:3px solid var(--line-strong);border-radius:4px;padding:4px 8px;margin-bottom:6px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.quote:disabled{cursor:default}
.mention{color:var(--primary);font-weight:600}
.picture{display:block;max-width:140px;max-height:140px;border-radius:var(--radius);margin:4px 0}
.reply-action{opacity:0;transition:opacity .15s}
.message:hover .reply-action,.reply-action:focus-visible{opacity:1}
.typing{padding-left:44px}
.composer{display:grid;gap:var(--sp-2);border-top:1px solid var(--line);padding:var(--sp-3) var(--sp-4)}
.composer p{margin:0}
.member-bar{display:flex;flex-wrap:wrap;align-items:center;gap:var(--sp-1)}
.member{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);color:inherit;font:inherit;font-size:var(--fs-sm);border-radius:999px;padding:2px 10px 2px 3px;cursor:pointer}
.member.active{border-color:var(--primary);background:var(--selected)}
.small-avatar{width:22px;height:22px;font-size:12px}
.reply-bar{display:flex;align-items:center;gap:var(--sp-2);font-size:var(--fs-sm);color:var(--muted);background:var(--hover);border-radius:var(--radius);padding:4px 4px 4px 10px}
.reply-bar span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mention-chips{display:flex;flex-wrap:wrap;gap:var(--sp-1)}
.send-row{display:flex;gap:var(--sp-2);align-items:flex-end}
.send-row .v-textarea{flex:1}
.turns-panel{display:grid;gap:var(--sp-2);border:1px solid var(--line);border-radius:var(--radius-lg);background:var(--surface);padding:var(--sp-3);max-height:min(78vh,820px);overflow:auto}
.turns-panel h3{margin:0;font-size:var(--fs-md)}
.turns-panel p{margin:0}
.turn{padding:var(--sp-2) var(--sp-3) var(--sp-3)}
@media(max-width:900px){.layout.wide{grid-template-columns:1fr}.window{height:70vh}}
</style>
