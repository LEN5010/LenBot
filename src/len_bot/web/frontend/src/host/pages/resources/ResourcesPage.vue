<script setup>
import { computed, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import { taskStorageApi } from '../../api/taskStorage.js'
import { formatTime } from '../../time.js'
import { continuationLabel, fileSize, rootLabel } from '../../spaceLabels.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import { taskStatus } from '../tasks/taskLabels.js'
import ResourceTaskDraft from '../tasks/ResourceTaskDraft.vue'
import TaskSpaceCard from '../../components/TaskSpaceCard.vue'
import TaskCleanupDialog from '../../components/TaskCleanupDialog.vue'

const route = useRoute(), router = useRouter()
const scenes = computed(() => host.state?.scenes || [])
const sceneFor = value => typeof value === 'string' && scenes.value.some(item => item.scene === value) ? value : scenes.value[0]?.scene
const scene = computed(() => sceneFor(route.query.scene))
const options = computed(() => scenes.value.map(item => ({ title: sceneName(item.scene), value: item.scene })))
const taskId = computed(() => /^[1-9][0-9]*$/.test(route.query.task || '') ? Number(route.query.task) : null)
const operator = ref(''), status = ref('all'), rows = ref([])
const environment = ref('all'), since = ref(''), before = ref(''), measure = ref(false), checked = ref([])
const resourceVersion = ref(0), spaceVersion = ref(0), operation = ref(null), cleanIds = ref([])
const cleaning = computed({ get: () => operation.value !== null, set: value => { if (!value) operation.value = null } })
const dirty = ref(false)
const { confirmLeave } = useUnsavedChanges(dirty)
onBeforeRouteUpdate((to, from) => {
  if (sceneFor(to.query.scene) === sceneFor(from.query.scene)) return true
  if (!confirmLeave()) return false
  dirty.value = false
  return true
})
const tasks = useResource(async (more = false) => ({ more: more === true,
  ...(await taskStorageApi.list(scene.value, { status: status.value, environment: environment.value, measure: measure.value,
    since: since.value ? new Date(since.value).getTime() / 1000 : null,
    before: before.value ? new Date(before.value).getTime() / 1000 : null,
    offset: more === true ? tasks.data.value.next_offset : 0, limit: 20 })) }), { immediate: false })
watch(tasks.data, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch([scene, status, environment, since, before], () => { rows.value = []; checked.value = []; if (scene.value) tasks.reload() }, { immediate: true })
const selected = computed(() => rows.value.find(item => item.id === taskId.value))
const selectable = computed(() => rows.value.filter(item => item.cleanable).map(item => item.id))
const allChecked = computed(() => selectable.value.length > 0 && selectable.value.every(id => checked.value.includes(id)))
function selectAll(value) { checked.value = value ? [...selectable.value] : [] }
function readUsage() { measure.value = true; tasks.reload() }
function cleanup(mode) { cleanIds.value = [...checked.value]; operation.value = mode }
function filesChanged() { resourceVersion.value++; tasks.reload() }
function batchChanged() { spaceVersion.value++; filesChanged() }
function select(id) { router.push({ name: 'host-resources', query: { scene: scene.value, task: id || undefined } }) }
function created(task) {
  dirty.value = false
  router.push({ name: 'host-tasks', query: { scene: scene.value, id: task.id } })
}
</script>

<template>
  <HostPage title="资源" description="浏览文件、保留成果，或选作下一项任务的资料。">
    <template #actions><v-select :model-value="scene" :items="options" label="场景" density="compact" class="scene"
      @update:model-value="value => router.push({ name: 'host-resources', query: { scene: value } })" /></template>
    <div v-if="scene" class="resources">
      <aside class="surface task-list">
        <v-btn :variant="taskId === null ? 'tonal' : 'text'" @click="select(null)">本场景共享资料</v-btn>
        <v-select v-model="status" :items="[{title:'全部任务',value:'all'},{title:'进行中',value:'active'},{title:'已完成',value:'done'},{title:'失败',value:'failed'},{title:'已取消',value:'cancelled'}]" density="compact" label="任务状态" hide-details />
        <v-select v-model="environment" :items="[{title:'全部环境',value:'all'},{title:'保留续接',value:'retained'},{title:'已放弃续接',value:'discarded'}]" label="续接状态" density="compact" hide-details />
        <v-text-field v-model="since" type="datetime-local" label="创建不早于（本机时间）" density="compact" hide-details />
        <v-text-field v-model="before" type="datetime-local" label="创建早于（本机时间）" density="compact" hide-details />
        <v-btn variant="text" size="small" :loading="tasks.loading.value" @click="readUsage">{{ measure ? '刷新用量' : '计算列表用量' }}</v-btn>
        <v-checkbox :model-value="allChecked" label="选择已显示的可清理任务" density="compact" hide-details @update:model-value="selectAll" />
        <ErrorNote v-if="tasks.error.value" title="读取任务失败" :error="tasks.error.value" />
        <v-list density="compact" nav>
          <v-list-item v-for="item in rows" :key="item.id" :active="taskId === item.id" @click="select(item.id)">
            <template #prepend><v-checkbox-btn v-model="checked" :value="item.id" :disabled="!item.cleanable" :aria-label="`选择任务 ${item.id}`" @click.stop /></template>
            <v-list-item-title class="goal">{{ item.goal }}</v-list-item-title>
            <v-list-item-subtitle>{{ taskStatus(item.status) }} · {{ formatTime(item.created) }}</v-list-item-subtitle>
            <small>{{ continuationLabel[item.continuation] }}</small>
            <ul v-if="item.roots" class="usage"><li v-for="root in item.roots" :key="root.kind">{{ rootLabel[root.kind] }}：{{ root.exists ? `${root.usage.files} 文件 · 分配 ${fileSize(root.usage.allocated_bytes)}` : '无目录' }}</li></ul>
            <ErrorNote v-if="item.usage_error" title="用量读取失败" :error="item.usage_error" />
          </v-list-item>
        </v-list>
        <v-btn v-if="tasks.data.value?.next_offset != null" variant="text" :loading="tasks.loading.value" @click="tasks.reload(true)">显示更多任务</v-btn>
        <div v-if="checked.length" class="batch"><p>已选 {{ checked.length }} 个任务</p>
          <p v-if="checked.length > 100">每批最多选择 100 个任务。</p>
          <v-btn size="small" variant="outlined" :disabled="checked.length > 100" @click="cleanup('temporary')">批量清理临时文件</v-btn>
          <v-btn size="small" variant="outlined" color="error" :disabled="checked.length > 100" @click="cleanup('environment')">批量释放环境</v-btn></div>
      </aside>
      <div class="files">
        <div class="heading"><div><h2>{{ taskId ? (selected?.goal || `任务 #${taskId}`) : '本场景共享资料' }}</h2>
          <RouterLink v-if="taskId" :to="{name:'host-tasks',query:{scene,id:taskId}}">查看任务与执行记录</RouterLink></div>
          <v-text-field v-model="operator" label="你的 QQ" inputmode="numeric" density="compact" hide-details class="operator" /></div>
        <TaskSpaceCard v-if="taskId" :key="`space:${scene}:${taskId}`" class="surface" :scene="scene" :task-id="taskId" :operator="operator" :version="spaceVersion" @changed="filesChanged" />
        <ResourceTaskDraft :key="scene" :scene="scene" :operator="operator" @dirty="value => dirty = value" @created="created">
          <template #default="{ select: selectFile }">
            <ResourceBrowser :key="`${scene}:${taskId}:${resourceVersion}`" :scene="scene" :task-id="taskId" :operator="operator" @select="selectFile" />
          </template>
        </ResourceTaskDraft>
      </div>
    </div>
    <v-dialog v-model="cleaning" max-width="1000" scrollable persistent>
      <TaskCleanupDialog v-if="cleaning" :scene="scene" :task-ids="cleanIds" :operator="operator" :operation="operation" @close="operation = null" @changed="batchChanged" />
    </v-dialog>
  </HostPage>
</template>

<style scoped>
.resources{display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px;align-items:start}.task-list{display:grid;gap:10px}.files{min-width:0;display:grid;gap:12px}.heading{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.heading h2,.goal{white-space:normal;overflow-wrap:anywhere}.operator{max-width:220px}.scene{min-width:200px}.usage{padding:0;list-style:none;font-size:12px;color:var(--muted);margin:4px 0}.batch{display:grid;gap:8px}.batch p{margin:0}@media(max-width:850px){.resources{grid-template-columns:1fr}}
</style>
