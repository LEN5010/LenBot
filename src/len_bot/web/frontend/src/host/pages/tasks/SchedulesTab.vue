<script setup>
import { computed, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'
import SchedulePicker from '../../components/SchedulePicker.vue'

const props = defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const state = useResource(() => api('/api/host/schedules/state'))
const settings = computed(() => state.data.value?.scenes.find(item => item.scene === props.scene) || null)
const status = ref('active'), rows = ref([])
const list = useResource(async more => ({ more: more === true, ...(await api('/api/host/schedules?' + queryString({
  scene: props.scene, status: status.value, offset: more === true ? list.data.value.next_offset : 0, limit: 20 }))) }))
watch(() => list.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch(status, () => list.reload())

const adding = ref(false), when = ref(''), note = ref(''), forWhom = ref('self'), other = ref('')
watch(() => adding.value && note.value !== '', value => emit('dirty', value), { immediate: true })
const create = useAction(), cancelling = useAction()
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))
async function submit() {
  const result = await create.run(() => api('/api/host/schedules?' + queryString({ scene: props.scene }), { method: 'POST',
    body: JSON.stringify({ requester: props.operator, when: when.value, note: note.value, for: forWhom.value === 'self' ? 'self' : other.value.trim() }) }))
  if (!result) return
  adding.value = false
  note.value = ''
  notify('已添加提醒')
  list.reload()
}
async function cancel(item) {
  if (!window.confirm(repeats(item) ? `停止这个重复提醒？\n${item.note}` : `取消这个提醒？\n${item.note}`)) return
  const result = await cancelling.run(() => api(`/api/host/schedules/${item.id}/cancel?` + queryString({ scene: props.scene }),
    { method: 'POST', body: JSON.stringify({ requester: props.operator }) }))
  if (!result) return
  notify('已取消')
  list.reload()
}
const repeats = item => item.interval_seconds !== null || item.cron !== null
const statusLabel = { pending: '等待中', blocked: '卡住了', delivered: '已提醒', cancelled: '已取消' }
function cadence(item) {
  if (item.cron !== null) {
    const [minute, hour, , , week] = item.cron.replace(/^cron:/, '').split(' ')
    const clock = /^\d+$/.test(hour) && /^\d+$/.test(minute) ? `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}` : ''
    const days = week === '*' ? '每天' : `每周${week.split(',').map(day => '日一二三四五六'[Number(day)] ?? day).join('、')}`
    return clock ? `${days} ${clock}` : item.cron
  }
  const value = item.interval_seconds
  if (value === null) return '一次'
  if (value % 86400 === 0) return `每 ${value / 86400} 天`
  if (value % 3600 === 0) return `每 ${value / 3600} 小时`
  return `每 ${value / 60} 分钟`
}
const statuses = [{ title: '进行中', value: 'active' }, { title: '全部', value: 'all' }, { title: '已提醒', value: 'delivered' }, { title: '已取消', value: 'cancelled' }]
</script>

<template>
  <ErrorNote v-if="state.error.value" title="读取提醒设置失败" :error="state.error.value" />
  <section class="surface schedules">
    <div class="head">
      <h2>提醒</h2>
      <v-select v-model="status" :items="statuses" density="compact" hide-details class="filter" />
      <v-btn color="primary" variant="tonal" :disabled="!settings?.enabled || !settings?.tool_allowed" @click="adding = true">添加提醒</v-btn>
    </div>
    <p v-if="settings && !settings.enabled" class="muted">本群没有开启提醒，可以在
      <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
    <p v-else-if="settings && !settings.tool_allowed" class="muted">角色没有允许提醒工具，可以在
      <RouterLink :to="{ name: 'host-capabilities', query: { scene } }">能力</RouterLink> 里打开。</p>
    <ErrorNote v-if="list.error.value" title="读取提醒失败" :error="list.error.value" />
    <ErrorNote v-if="cancelling.error.value" title="没有取消成功" :error="cancelling.error.value" />
    <p v-if="list.data.value && !rows.length" class="muted">没有提醒</p>
    <ul class="items">
      <li v-for="item in rows" :key="item.id">
        <div class="main">
          <p class="note">{{ item.note }}</p>
          <span class="muted">{{ cadence(item) }} · {{ statusLabel[item.status] || item.status }} ·
            {{ item.status === 'pending' ? '下次' : '时间' }} {{ formatTime(item.due_at, item.timezone) }} ·
            {{ item.requester === null ? 'Bot 自己定的' : `QQ ${item.requester} 定的` }}{{ item.target !== 'self' ? `，提醒 QQ ${item.target}` : '' }}</span>
          <p v-if="item.reason" class="reason">{{ item.reason }}</p>
          <DevOnly label="原始记录"><pre>{{ JSON.stringify(item, null, 2) }}</pre></DevOnly>
        </div>
        <v-btn v-if="['pending', 'blocked'].includes(item.status)" size="small" variant="text" color="error"
          :disabled="!validQQ" :loading="cancelling.busy.value" @click="cancel(item)">取消</v-btn>
      </li>
    </ul>
    <p v-if="rows.some(item => ['pending', 'blocked'].includes(item.status)) && !validQQ" class="muted">填写上方你的 QQ 后可以取消提醒。</p>
    <v-btn v-if="list.data.value?.next_offset != null" size="small" variant="text" :loading="list.loading.value" @click="list.reload(true)">显示更多</v-btn>
  </section>

  <v-dialog v-model="adding" max-width="560" scrollable>
    <v-card v-if="settings" title="添加提醒">
      <v-card-text class="form">
        <v-textarea v-model="note" label="提醒什么" rows="2" auto-grow />
        <SchedulePicker v-model="when" :timezone="settings.timezone" />
        <v-btn-toggle v-model="forWhom" mandatory density="comfortable" color="primary">
          <v-btn value="self">提醒我</v-btn><v-btn value="other">提醒别人</v-btn></v-btn-toggle>
        <v-text-field v-if="forWhom === 'other'" v-model="other" label="对方 QQ" inputmode="numeric" />
        <p v-if="!validQQ" class="problem">先在上方填写你的 QQ</p>
        <ErrorNote v-if="create.error.value" title="没有添加成功" :error="create.error.value" />
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn @click="adding = false">取消</v-btn>
        <v-btn color="primary" :loading="create.busy.value" :disabled="!validQQ || !note.trim() || !when || (forWhom === 'other' && !other.trim())" @click="submit">添加</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.schedules{display:grid;gap:10px}
.head{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.head h2{margin-right:auto !important}
.filter{max-width:160px}
.items{list-style:none;margin:0;padding:0;display:grid}
.items li{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.items li:last-child{border-bottom:0}
.main{min-width:0;display:grid;gap:2px}
.note{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.reason{margin:0;color:var(--warning-text);font-size:13px}
.form{display:grid;gap:14px}
.problem{color:var(--error-text);margin:0}
</style>
