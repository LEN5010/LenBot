<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, queryString } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useHostEvents } from '../../events.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import LiveStatus from '../../components/LiveStatus.vue'
import NewTask from './NewTask.vue'
import TaskDetail from './TaskDetail.vue'
import { taskStatus } from './taskLabels.js'

const props = defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const route = useRoute(), router = useRouter()
const state = useResource(() => api('/api/host/tasks/state'))
const settings = computed(() => state.data.value?.scenes.find(item => item.scene === props.scene) || null)
const filter = ref('active'), rows = ref([])
const list = useResource(async more => ({ more: more === true, ...(await api('/api/host/tasks?' + queryString({
  scene: props.scene, status: filter.value, offset: more === true ? list.data.value.next_offset : 0, limit: 20 }))) }))
watch(() => list.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch(filter, () => list.reload())
const selected = computed(() => /^[1-9][0-9]*$/.test(route.query.id ?? '') ? Number(route.query.id) : null)

// Change notices re-read the list and the open task; drafts in the detail stay as typed.
const version = ref(0)
const events = useHostEvents(async () => {
  version.value++
  await Promise.all([state.reload(), list.reload()])
})

const creating = ref(false), detailDirty = ref(false), newDirty = ref(false)
watch(() => detailDirty.value || newDirty.value, value => emit('dirty', value), { immediate: true })
function open(id) {
  if (id === selected.value) return
  if (detailDirty.value && !window.confirm('放弃没提交的内容？')) return
  detailDirty.value = false
  router.push({ name: 'host-tasks', query: { ...route.query, id: String(id) } })
}
function created(task) {
  creating.value = false
  newDirty.value = false
  list.reload()
  open(task.id)
}
const filters = [{ title: '进行中', value: 'active' }, { title: '全部', value: 'all' }, { title: '已完成', value: 'done' },
  { title: '失败', value: 'failed' }, { title: '已取消', value: 'cancelled' }]
const accepting = computed(() => state.data.value?.configured && state.data.value.accepting && settings.value?.enabled)
</script>

<template>
  <ErrorNote v-if="state.error.value" title="读取任务状态失败" :error="state.error.value" />
  <template v-if="state.data.value">
    <p v-if="!state.data.value.configured" class="surface muted">还没有启用独立任务，可以在
      <RouterLink :to="{ name: 'host-capabilities', query: { tab: 'tasks' } }">能力 › 独立任务</RouterLink> 里设置。</p>
    <ErrorNote v-if="state.data.value.error" title="任务执行环境出错了" :error="state.data.value.error" />
    <div class="layout">
      <section class="surface list">
        <div class="head">
          <v-select v-model="filter" :items="filters" density="compact" hide-details class="filter" />
          <v-btn color="primary" variant="tonal" size="small" :disabled="!accepting" @click="creating = true">新建任务</v-btn>
        </div>
        <p v-if="state.data.value.configured && settings && !settings.enabled" class="muted">本群没有开启任务，可以在
          <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
        <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
        <ErrorNote v-if="list.error.value" title="读取任务列表失败" :error="list.error.value" />
        <p v-if="list.data.value && !rows.length" class="muted">没有任务</p>
        <v-list density="compact" nav class="rows">
          <v-list-item v-for="item in rows" :key="item.id" :active="item.id === selected" @click="open(item.id)">
            <v-list-item-title class="goal">{{ item.goal }}</v-list-item-title>
            <v-list-item-subtitle>{{ taskStatus(item.status) }} · {{ formatTime(item.created, settings?.timezone) }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
        <v-btn v-if="list.data.value?.next_offset != null" size="small" variant="text" :loading="list.loading.value" @click="list.reload(true)">显示更多</v-btn>
      </section>
      <div class="detail">
        <TaskDetail v-if="selected" :key="selected" :id="selected" :scene="scene" :operator="operator" :version="version"
          :service="state.data.value" :settings="settings" @dirty="value => detailDirty = value" @changed="list.reload()" />
        <p v-else class="surface muted">选择一个任务查看详情</p>
      </div>
    </div>
  </template>
  <v-dialog v-model="creating" max-width="640" scrollable>
    <NewTask v-if="creating" :scene="scene" :operator="operator" @dirty="value => newDirty = value" @created="created" @close="creating = false" />
  </v-dialog>
</template>

<style scoped>
.layout{display:grid;grid-template-columns:minmax(240px,340px) 1fr;gap:16px;align-items:start}
.list{display:grid;gap:8px;padding:12px}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px}
.filter{max-width:140px}
.rows{padding:0;background:transparent}
.goal{white-space:normal;overflow-wrap:anywhere}
.detail{min-width:0}
@media(max-width:860px){.layout{grid-template-columns:1fr}}
</style>
