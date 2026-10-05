<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { tasksApi } from '../../api/tasks.js'
import { useResource } from '../../../composables/useResource.js'
import { useHostEvents } from '../../events.js'
import { formatTime } from '../../time.js'
import { confirm } from '../../../composables/useConfirm.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LiveStatus from '../../ui/LiveStatus.vue'
import LoadMore from '../../ui/LoadMore.vue'
import NewTask from './NewTask.vue'
import TaskDetail from './TaskDetail.vue'

const props = defineProps({ scene: { type: String, required: true }, operator: { type: String, required: true } })
const emit = defineEmits(['dirty'])
const route = useRoute(), router = useRouter()
const state = useResource(() => tasksApi.state())
const settings = computed(() => state.data.value?.scenes.find(item => item.scene === props.scene) || null)
const filter = ref('active'), rows = ref([])
const list = useResource(async more => ({ more: more === true, ...(await tasksApi.list(props.scene, {
  status: filter.value, offset: more === true ? list.data.value.next_offset : 0, limit: 20 })) }))
watch(() => list.data.value, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch(filter, () => list.reload())
const selected = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(route.query.id ?? '') ? Number(route.query.id) : null)

// Change notices re-read the list and the open task; drafts in the detail stay as typed.
const version = ref(0)
const events = useHostEvents(async () => {
  version.value++
  await Promise.all([state.reload(), list.reload()])
})

const creating = ref(false), detailDirty = ref(false), newDirty = ref(false)
watch(() => detailDirty.value || newDirty.value, value => emit('dirty', value), { immediate: true })
async function open(id) {
  if (id === selected.value) return
  if (detailDirty.value && !await confirm({ title: '放弃没提交的内容？', confirmLabel: '放弃', danger: true })) return
  detailDirty.value = false
  router.push({ name: 'host-tasks', query: { ...route.query, id: id === null ? undefined : String(id) } })
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
  <ResourceState :resource="state" error-title="读取任务状态失败" v-slot="{ data }">
    <v-alert v-if="!data.configured" type="info">还没有启用独立任务，可以在
      <RouterLink :to="{ name: 'host-capabilities', query: { tab: 'tasks' } }">能力 › 独立任务</RouterLink> 里设置。</v-alert>
    <ErrorNote v-if="data.error" title="任务执行环境出错了" :error="data.error" />
    <MasterDetail :selected="selected !== null" list-width="340px" @back="open(null)">
      <template #list>
        <Panel title="任务" flush>
          <template #actions>
            <LiveStatus :status="events.status.value" @reconnect="events.reconnect" />
            <v-btn color="primary" variant="tonal" size="small" :disabled="!accepting" @click="creating = true">新建</v-btn>
          </template>
          <div class="list">
            <v-select v-model="filter" :items="filters" aria-label="筛选" />
            <p v-if="data.configured && settings && !settings.enabled" class="muted small">本群没有开启任务，可以在
              <RouterLink :to="{ name: 'host-scenes', query: { scene, tab: 'settings' } }">群聊设置</RouterLink> 里打开。</p>
            <ResourceState :resource="list" error-title="读取任务列表失败" :empty="!rows.length" empty-text="没有任务" compact>
              <ObjectList>
                <ObjectRow v-for="item in rows" :key="item.id" :title="item.goal" :subtitle="formatTime(item.created, settings?.timezone)"
                  clickable :active="item.id === selected" @click="open(item.id)">
                  <template #meta><StatusBadge dot kind="task" :value="item.status" /></template>
                </ObjectRow>
              </ObjectList>
              <LoadMore v-if="list.data.value?.next_offset != null" :loading="list.loading.value" @more="list.reload(true)" />
            </ResourceState>
          </div>
        </Panel>
      </template>
      <template #placeholder>从左边选一个任务查看详情。</template>
      <TaskDetail v-if="selected" :key="selected" :id="selected" :scene="scene" :operator="operator" :version="version"
        :service="data" :settings="settings" @dirty="value => detailDirty = value" @changed="list.reload()" @created="created" />
    </MasterDetail>
  </ResourceState>
  <NewTask v-if="creating" :scene="scene" :operator="operator" @dirty="value => newDirty = value" @created="created" @close="creating = false" />
</template>

<style scoped>
.list{display:grid;gap:var(--sp-2);padding:0 var(--sp-2) var(--sp-2)}
.list p{margin:0;padding:0 var(--sp-2)}
</style>
