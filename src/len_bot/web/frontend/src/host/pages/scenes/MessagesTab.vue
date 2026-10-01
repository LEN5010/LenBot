<script setup>
import { computed } from 'vue'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useHostEvents } from '../../events.js'
import { messageLabel, noticeLabel } from '../../labels.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import LiveStatus from '../../components/LiveStatus.vue'
import DevOnly from '../../components/DevOnly.vue'
import AudioList from './AudioList.vue'

const props = defineProps({ scene: { type: String, required: true } })
const path = `/api/host/scenes/${encodeURIComponent(props.scene)}`
const state = useResource(() => api(path))
const notices = useResource(() => api(`${path}/notices`))
const events = useHostEvents(() => Promise.all([state.reload(), notices.reload()]))

const timezone = computed(() => state.data.value?.timezone)
const items = computed(() => {
  const messages = [...(state.data.value?.messages || [])].sort((a, b) => a.seq - b.seq)
  const since = messages[0]?.time ?? 0
  const notes = (notices.data.value?.items || []).filter(item => item.time >= since)
    .map(item => ({ key: `n${item.id}`, notice: item, time: item.time }))
  return [...messages.map(item => ({ key: `m${item.seq}`, message: item, time: item.time })), ...notes]
    .sort((a, b) => a.time - b.time)
})
// One scene runs one turn at a time, so a Bot message saved inside a turn's
// time span belongs to that turn.
function turnOf(message) {
  return (state.data.value?.turns || []).find(turn => turn.started <= message.time && (turn.ended ?? Infinity) >= message.time)
}
const name = message => message.is_self ? state.data.value.persona.name : (message.sender.card || message.sender.nickname || message.sender.uid)
const imageUrl = (message, image) => `${path}/messages/${message.seq}/images/${image.image_index}`
</script>

<template>
  <div class="page-stack">
    <div class="tab-toolbar"><LiveStatus :status="events.status.value" @reconnect="events.reconnect" /></div>
    <ErrorNote v-if="state.error.value" title="读取聊天记录失败" :error="state.error.value" />
    <ErrorNote v-if="notices.error.value" title="读取平台通知失败" :error="notices.error.value" />
    <section v-if="state.data.value" class="surface chat">
      <p v-if="!items.length" class="empty-state">还没有消息</p>
      <ol v-else class="chat-list">
        <li v-for="item in items" :key="item.key" :class="item.message ? { mine: item.message.is_self } : 'notice'">
          <template v-if="item.notice">
            <span>{{ item.notice.raw.user_id ? `QQ ${item.notice.raw.user_id} ` : '' }}{{ noticeLabel(item.notice.kind) }} · {{ formatTime(item.time, timezone) }}</span>
            <DevOnly label="平台原文"><pre>{{ JSON.stringify(item.notice.raw, null, 2) }}</pre></DevOnly>
          </template>
          <template v-else>
            <div class="meta"><strong>{{ name(item.message) }}</strong>
              <span v-if="!item.message.is_self">QQ {{ item.message.sender.uid }}</span>
              <span>{{ formatTime(item.time, timezone) }}</span>
              <span v-if="item.message.recalled" class="flag">已撤回</span>
              <span v-if="item.message.is_self && item.message.send_status !== 'sent'"
                :class="['flag', item.message.send_status]">{{ messageLabel(item.message.send_status) }}</span></div>
            <div class="bubble">{{ item.message.text }}</div>
            <div v-if="item.message.images.length" class="images">
              <a v-for="image in item.message.images" :key="image.image_index" :href="imageUrl(item.message, image)" target="_blank" rel="noopener">
                <img :src="imageUrl(item.message, image)" :alt="image.description || '图片'" loading="lazy" /></a>
            </div>
            <RouterLink v-if="item.message.is_self && turnOf(item.message)" class="why"
              :to="{ name: 'host-logs', query: { scene, turn: turnOf(item.message).id } }">为什么这么说</RouterLink>
            <DevOnly label="消息原始数据"><pre>{{ JSON.stringify(item.message, null, 2) }}</pre></DevOnly>
          </template>
        </li>
      </ol>
    </section>
    <AudioList :scene="scene" />
  </div>
</template>

<style scoped>
.tab-toolbar{display:flex;justify-content:flex-end}
.chat-list{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.chat-list li{max-width:min(720px,90%);min-width:0}
.chat-list li.mine{justify-self:end;text-align:right}
.chat-list li.notice{justify-self:center;max-width:100%;text-align:center;font-size:12px;color:var(--muted)}
.meta{display:flex;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--muted);align-items:baseline}
.mine .meta{justify-content:flex-end}
.meta strong{color:var(--ink);font-size:13px}
.flag{border-radius:4px;padding:0 6px;background:var(--chip-bg)}
.flag.failed{background:var(--error-bg);color:var(--error-text)}
.flag.unconfirmed{background:var(--warning-bg);color:var(--warning-text)}
.bubble{display:inline-block;text-align:left;margin-top:4px;padding:8px 12px;border-radius:12px;background:var(--message-avatar-bg);white-space:pre-wrap;overflow-wrap:anywhere}
.mine .bubble{background:var(--bot-avatar-bg)}
.images{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.mine .images{justify-content:flex-end}
.images img{max-width:180px;max-height:180px;border-radius:8px;border:1px solid var(--line);display:block}
.why{display:block;font-size:12px;margin-top:4px}
</style>
