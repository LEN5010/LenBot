<script setup>
import { ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'

const props = defineProps({ scene: { type: String, required: true } })
const rows = ref([])
const page = useResource(async more => ({ more: more === true, ...(await api('/api/host/schedules/proactive?' + queryString({
  scene: props.scene, offset: more === true ? page.data.value.next_offset : 0 }))) }))
watch(() => page.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
const outcomes = { silent: '叫醒后没有开口', answered: '有人接话', ignored: '没人接话', unobserved: '没法判断' }
const at = value => formatTime(value, page.data.value?.timezone)
const hours = seconds => {
  const minutes = Math.round(seconds / 60)
  return minutes % 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟` : `${minutes / 60} 小时`
}
function outcome(item) {
  if (item.assessment === 'arrival_count') return item.outcome || '进行中'
  if (item.outcome) return outcomes[item.outcome] || item.outcome
  if (item.turn_ended === null) return '进行中'
  return '等待判断'
}
</script>

<template>
  <section class="surface proactive">
    <div class="head">
      <h2>主动开话题</h2>
      <v-btn size="small" variant="text" :loading="page.loading.value" @click="page.reload()">刷新</v-btn>
    </div>
    <ErrorNote v-if="page.error.value" title="读取主动开话题记录失败" :error="page.error.value" />
    <template v-if="page.data.value">
      <p v-if="!page.data.value.settings" class="muted">本群没有开启主动开话题，可以在
        <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
      <template v-else>
        <p>群里安静 {{ hours(page.data.value.settings.idle_seconds) }}后，Bot 会在
          {{ page.data.value.settings.start.slice(0, 5) }}–{{ page.data.value.settings.end.slice(0, 5) }} 之间找个话题聊聊，每天最多一次。</p>
        <p class="muted">下次最早 {{ page.data.value.next_at === null ? '—' : at(page.data.value.next_at) }}{{ page.data.value.next_reason ? `（${page.data.value.next_reason}）` : '' }}</p>
      </template>
      <v-alert v-if="page.data.value.pause" type="info" variant="tonal" density="compact">
        最近两次开话题都没人接，暂停到 {{ at(page.data.value.pause.until) }}。</v-alert>
      <p v-if="!rows.length" class="muted">还没有主动开过话题</p>
      <ul class="wakes">
        <li v-for="item in rows" :key="item.id">
          <span>{{ at(item.woke_at) }}</span>
          <strong>{{ outcome(item) }}</strong>
          <RouterLink :to="{ name: 'host-logs', query: { scene, turn: item.turn_id } }">查看这一轮</RouterLink>
        </li>
      </ul>
      <v-btn v-if="page.data.value.next_offset !== null" size="small" variant="text" :loading="page.loading.value" @click="page.reload(true)">显示更多</v-btn>
    </template>
  </section>
</template>

<style scoped>
.proactive{display:grid;gap:8px}
.proactive p{margin:0}
.head{display:flex;justify-content:space-between;align-items:center}
.wakes{list-style:none;margin:0;padding:0;display:grid}
.wakes li{display:grid;grid-template-columns:120px 1fr auto;gap:12px;padding:8px 0;border-bottom:1px solid var(--line);align-items:center}
.wakes li:last-child{border-bottom:0}
</style>
