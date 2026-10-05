<script setup>
import { computed } from 'vue'
import { api } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useHostEvents } from '../../events.js'
import { noticeLabel } from '../../labels.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import DevOnly from '../../ui/DevOnly.vue'
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
  <Panel title="最近消息">
    <template #actions><LiveStatus :status="events.status.value" @reconnect="events.reconnect" /></template>
    <ErrorNote v-if="notices.error.value" title="读取平台通知失败" :error="notices.error.value" @retry="notices.reload()" />
    <ResourceState :resource="state" error-title="读取聊天记录失败" :empty="!items.length" empty-text="还没有消息">
      <ol class="chat-list">
        <li v-for="item in items" :key="item.key" :class="item.message ? { mine: item.message.is_self } : 'notice'">
          <template v-if="item.notice">
            <span>{{ item.notice.raw.user_id ? `平台账号 ${item.notice.raw.user_id} ` : '' }}{{ noticeLabel(item.notice.kind) }} · {{ formatTime(item.time, timezone) }}</span>
            <DevOnly label="平台原文" :json="item.notice.raw" />
          </template>
          <template v-else>
            <div class="meta"><strong>{{ name(item.message) }}</strong>
              <span v-if="!item.message.is_self">平台账号 {{ item.message.sender.uid }}</span>
              <span>{{ formatTime(item.time, timezone) }}</span>
              <StatusBadge v-if="item.message.recalled" text="已撤回" />
              <StatusBadge v-if="item.message.is_self && item.message.send_status !== 'sent'" kind="message" :value="item.message.send_status" /></div>
            <div class="bubble">{{ item.message.text }}</div>
            <div v-if="item.message.images.length" class="images">
              <a v-for="image in item.message.images" :key="image.image_index" :href="imageUrl(item.message, image)" target="_blank" rel="noopener">
                <img :src="imageUrl(item.message, image)" :alt="image.description || '图片'" loading="lazy" /></a>
            </div>
            <RouterLink v-if="item.message.is_self && turnOf(item.message)" class="why"
              :to="{ name: 'host-logs', query: { scene, turn: turnOf(item.message).id } }">为什么这么说</RouterLink>
            <DevOnly label="消息原始数据" :json="item.message" />
          </template>
        </li>
      </ol>
    </ResourceState>
  </Panel>
  <AudioList :scene="scene" />
</template>

<style scoped>
.chat-list{list-style:none;margin:0;padding:0;display:grid;gap:var(--sp-3)}
.chat-list li{max-width:min(720px,90%);min-width:0}
.chat-list li.mine{justify-self:end;text-align:right}
.chat-list li.notice{justify-self:center;max-width:100%;text-align:center;font-size:var(--fs-xs);color:var(--muted)}
.meta{display:flex;gap:var(--sp-2);flex-wrap:wrap;font-size:var(--fs-xs);color:var(--muted);align-items:center}
.mine .meta{justify-content:flex-end}
.meta strong{color:var(--ink);font-size:var(--fs-sm)}
.bubble{display:inline-block;text-align:left;margin-top:var(--sp-1);padding:var(--sp-2) var(--sp-3);border-radius:var(--radius-lg);background:var(--selected);white-space:pre-wrap;overflow-wrap:anywhere}
.mine .bubble{background:var(--primary-bg)}
.images{display:flex;gap:var(--sp-2);flex-wrap:wrap;margin-top:var(--sp-1)}
.mine .images{justify-content:flex-end}
.images img{max-width:180px;max-height:180px;border-radius:var(--radius);border:1px solid var(--line);display:block}
.why{display:block;font-size:var(--fs-xs);margin-top:var(--sp-1)}
</style>
