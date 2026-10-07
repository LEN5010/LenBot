<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { mdiFolderAccountOutline } from '@mdi/js'
import { useResource } from '../../../composables/useResource.js'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import { taskStorageApi } from '../../api/taskStorage.js'
import { formatTime } from '../../time.js'
import { continuationLabel, fileSize, rootLabel } from '../../spaceLabels.js'
import HostPage from '../../ui/HostPage.vue'
import Panel from '../../ui/Panel.vue'
import MasterDetail from '../../ui/MasterDetail.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import LoadMore from '../../ui/LoadMore.vue'
import Fold from '../../ui/Fold.vue'
import OperatorField from '../../components/OperatorField.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import ResourceTaskDraft from '../tasks/ResourceTaskDraft.vue'
import TaskSpaceCard from '../../components/TaskSpaceCard.vue'
import TaskCleanupDialog from '../../components/TaskCleanupDialog.vue'
import StoragePoolCard from '../../components/StoragePoolCard.vue'

const route = useRoute(), router = useRouter()
const { scene } = useCurrentScene()
const taskId = computed(() => /^[1-9][0-9]*$/.test(route.query.task || '') ? Number(route.query.task) : null)
const operator = computed(() => host.operator)
const status = ref('all'), rows = ref([])
const environment = ref('all'), since = ref(''), before = ref(''), measure = ref(false), checked = ref([])
const resourceVersion = ref(0), spaceVersion = ref(0), operation = ref(null), cleanIds = ref([])
const cleaning = computed({ get: () => operation.value !== null, set: value => { if (!value) operation.value = null } })
const dirty = ref(false)
useUnsavedChanges(dirty, { keep: ['task'], onDiscard: () => { dirty.value = false } })
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
const select = id => router.push({ name: 'host-resources', query: { scene: scene.value, task: id === null ? undefined : String(id) } })
function created(task) {
  dirty.value = false
  router.push({ name: 'host-tasks', query: { scene: scene.value, id: task.id } })
}
</script>

<template>
  <HostPage title="资源" description="浏览文件、保留成果，或选作下一个任务的资料。" wide>
    <template #actions><OperatorField /></template>
    <StoragePoolCard :version="resourceVersion + spaceVersion" />
    <MasterDetail v-if="scene" :selected="route.query.task !== undefined" default-detail list-width="340px" @back="select(null)">
      <template #list>
        <Panel title="任务" flush>
          <template #actions><v-btn variant="text" size="small" :loading="tasks.loading.value" @click="readUsage">{{ measure ? '刷新用量' : '计算用量' }}</v-btn></template>
          <div class="list">
            <ObjectList>
              <ObjectRow title="本群共享资料" clickable :active="taskId === null" @click="select('shared')">
                <template #prepend><v-icon :icon="mdiFolderAccountOutline" size="20" color="secondary" /></template>
              </ObjectRow>
            </ObjectList>
            <Fold label="筛选">
              <v-select v-model="status" :items="[{title:'全部任务',value:'all'},{title:'进行中',value:'active'},{title:'已完成',value:'done'},{title:'失败',value:'failed'},{title:'已取消',value:'cancelled'}]" label="任务状态" />
              <v-select v-model="environment" :items="[{title:'全部',value:'all'},{title:'保留续接',value:'retained'},{title:'已放弃续接',value:'discarded'}]" label="续接状态" />
              <v-text-field v-model="since" type="datetime-local" label="创建不早于" />
              <v-text-field v-model="before" type="datetime-local" label="创建早于" />
            </Fold>
            <v-checkbox :model-value="allChecked" label="选中已显示的可清理任务" :disabled="!selectable.length" @update:model-value="selectAll" />
            <ResourceState :resource="tasks" error-title="读取任务失败" :empty="!rows.length" empty-text="没有任务" compact>
              <ObjectList>
                <ObjectRow v-for="item in rows" :key="item.id" :title="item.goal" clickable :active="taskId === item.id" @click="select(item.id)"
                  :subtitle="`${formatTime(item.created)} · ${continuationLabel[item.continuation]}`">
                  <template #prepend><v-checkbox-btn v-model="checked" :value="item.id" :disabled="!item.cleanable" :aria-label="`选择任务 ${item.id}`" density="compact" @click.stop /></template>
                  <ul v-if="item.roots" class="usage"><li v-for="root in item.roots" :key="root.kind">{{ rootLabel[root.kind] }}：{{ root.exists ? `${root.usage.files} 个文件 · ${fileSize(root.usage.allocated_bytes)}` : '无目录' }}</li></ul>
                  <ErrorNote v-if="item.usage_error" title="用量读取失败" :error="item.usage_error" />
                  <template #meta><StatusBadge dot kind="task" :value="item.status" /></template>
                </ObjectRow>
              </ObjectList>
              <LoadMore v-if="tasks.data.value?.next_offset != null" :loading="tasks.loading.value" @more="tasks.reload(true)" />
            </ResourceState>
          </div>
          <template v-if="checked.length" #footer>
            <span class="small">已选 {{ checked.length }} 个<template v-if="checked.length > 100">，每批最多 100 个</template></span>
            <v-btn size="small" variant="outlined" :disabled="checked.length > 100" @click="cleanup('temporary')">清理临时文件</v-btn>
            <v-btn size="small" variant="text" color="error" :disabled="checked.length > 100" @click="cleanup('environment')">释放环境</v-btn>
          </template>
        </Panel>
      </template>

      <Panel :title="taskId ? (selected?.goal || `任务 #${taskId}`) : '本群共享资料'">
        <template v-if="taskId" #actions><v-btn size="small" variant="text" :to="{ name: 'host-tasks', query: { scene, id: taskId } }">查看任务</v-btn></template>
        <TaskSpaceCard v-if="taskId" :key="`space:${scene}:${taskId}`" :scene="scene" :task-id="taskId" :operator="operator" :version="spaceVersion" @changed="filesChanged" />
        <p v-else class="muted">新建任务时可以选这里的资料。</p>
      </Panel>
      <ResourceTaskDraft :key="scene" :scene="scene" :operator="operator" @dirty="value => dirty = value" @created="created">
        <template #default="{ select: selectFile }">
          <ResourceBrowser :key="`${scene}:${taskId}:${resourceVersion}`" :scene="scene" :task-id="taskId" :operator="operator" @select="selectFile" @changed="spaceVersion++" />
        </template>
      </ResourceTaskDraft>
    </MasterDetail>
    <TaskCleanupDialog v-if="cleaning" :scene="scene" :task-ids="cleanIds" :operator="operator" :operation="operation" @close="operation = null" @changed="batchChanged" />
  </HostPage>
</template>

<style scoped>
.list{display:grid;gap:var(--sp-2);padding:0 var(--sp-2) var(--sp-2)}
.usage{padding:0;list-style:none;font-size:var(--fs-xs);color:var(--muted);margin:0}
</style>
