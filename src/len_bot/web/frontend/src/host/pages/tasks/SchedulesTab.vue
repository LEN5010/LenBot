<script setup>
import { computed, ref, watch } from 'vue'
import { schedulesApi } from '../../api/schedules.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import FormDialog from '../../ui/FormDialog.vue'
import LoadMore from '../../ui/LoadMore.vue'
import DevOnly from '../../ui/DevOnly.vue'
import SchedulePicker from '../../components/SchedulePicker.vue'

const props = defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const state = useResource(() => schedulesApi.state())
const settings = computed(() => state.data.value?.scenes.find(item => item.scene === props.scene) || null)
const status = ref('active'), rows = ref([])
const list = useResource(async more => ({ more: more === true, ...(await schedulesApi.list(props.scene, {
  status: status.value, offset: more === true ? list.data.value.next_offset : 0, limit: 20 })) }))
watch(() => list.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch(status, () => list.reload())

const adding = ref(false), when = ref(''), note = ref(''), forWhom = ref('self'), other = ref('')
watch(() => adding.value && note.value !== '', value => emit('dirty', value), { immediate: true })
const create = useAction(), cancelling = useAction()
const validIdentity = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))
async function submit() {
  const result = await create.run(() => schedulesApi.create(props.scene, { requester: props.operator, when: when.value, note: note.value,
    for: forWhom.value === 'self' ? 'self' : other.value.trim() }))
  if (!result) return
  adding.value = false
  note.value = ''
  notify('已添加提醒')
  list.reload()
}
async function cancel(item) {
  if (!await confirm({ title: repeats(item) ? '停止这个重复提醒？' : '取消这个提醒？', text: item.note, confirmLabel: repeats(item) ? '停止' : '取消提醒', danger: true })) return
  const result = await cancelling.run(() => schedulesApi.cancel(props.scene, item.id, props.operator))
  if (!result) return
  notify('已取消')
  list.reload()
}
const repeats = item => item.interval_seconds !== null || item.cron !== null
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
  <ErrorNote v-if="state.error.value" title="读取提醒设置失败" :error="state.error.value" @retry="state.reload()" />
  <Panel title="提醒">
    <template #actions>
      <v-select v-model="status" :items="statuses" class="filter" aria-label="筛选" />
      <v-btn color="primary" variant="tonal" :disabled="!settings?.enabled || !settings?.tool_allowed" @click="adding = true">添加提醒</v-btn>
    </template>
    <p v-if="settings && !settings.enabled" class="muted">本群没有开启提醒，可以在
      <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
    <p v-else-if="settings && !settings.tool_allowed" class="muted">角色没有允许提醒工具，可以在
      <RouterLink :to="{ name: 'host-capabilities', query: { scene } }">能力</RouterLink> 里打开。</p>
    <ErrorNote v-if="cancelling.error.value" title="没有取消成功" :error="cancelling.error.value" />
    <ResourceState :resource="list" error-title="读取提醒失败" :empty="!rows.length" empty-text="没有提醒" compact>
      <ObjectList divided>
        <ObjectRow v-for="item in rows" :key="item.id" :title="item.note"
          :subtitle="`${cadence(item)} · ${item.status === 'pending' ? '下次' : '时间'} ${formatTime(item.due_at, item.timezone)} · ${item.requester === null ? 'Bot 自己定的' : `平台账号 ${item.requester} 定的`}${item.target !== 'self' ? `，提醒 平台账号 ${item.target}` : ''}`">
          <p v-if="item.reason" class="reason">{{ item.reason }}</p>
          <DevOnly label="原始记录" :json="item" />
          <template #meta><StatusBadge kind="schedule" :value="item.status" /></template>
          <template #actions>
            <v-btn v-if="['pending', 'blocked'].includes(item.status)" size="small" variant="text" color="error"
              :disabled="!validIdentity" :loading="cancelling.busy.value" @click="cancel(item)">取消</v-btn>
          </template>
        </ObjectRow>
      </ObjectList>
      <p v-if="rows.some(item => ['pending', 'blocked'].includes(item.status)) && !validIdentity" class="muted small">在页面上方填写你的 平台账号 后可以取消提醒。</p>
      <LoadMore v-if="list.data.value?.next_offset != null" :loading="list.loading.value" @more="list.reload(true)" />
    </ResourceState>
  </Panel>

  <FormDialog v-model="adding" title="添加提醒" :busy="create.busy.value">
    <template v-if="settings">
      <v-textarea v-model="note" label="提醒什么" rows="2" auto-grow />
      <SchedulePicker v-model="when" :timezone="settings.timezone" />
      <v-btn-toggle v-model="forWhom" mandatory>
        <v-btn value="self">提醒我</v-btn><v-btn value="other">提醒别人</v-btn></v-btn-toggle>
      <v-text-field v-if="forWhom === 'other'" v-model="other" label="对方 平台账号"  />
      <p v-if="!validIdentity" class="problem">先在页面上方填写你的 平台账号</p>
      <ErrorNote v-if="create.error.value" title="没有添加成功" :error="create.error.value" />
    </template>
    <template #actions>
      <v-btn color="primary" :loading="create.busy.value" :disabled="!validIdentity || !note.trim() || !when || (forWhom === 'other' && !other.trim())" @click="submit">添加</v-btn>
    </template>
  </FormDialog>
</template>

<style scoped>
.filter{width:140px;flex:none}
.reason{margin:0;color:var(--warning);font-size:var(--fs-sm)}
p{margin:0}
</style>
