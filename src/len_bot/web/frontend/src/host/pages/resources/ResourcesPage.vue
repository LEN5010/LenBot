<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { sceneName } from '../../../api.js'
import { useResource } from '../../../composables/useResource.js'
import { host } from '../../store.js'
import { tasksApi } from '../../api/tasks.js'
import { formatTime } from '../../time.js'
import HostPage from '../../components/HostPage.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import { taskStatus } from '../tasks/taskLabels.js'

const route = useRoute(), router = useRouter()
const scenes = computed(() => host.state?.scenes || [])
const scene = computed(() => typeof route.query.scene === 'string' && scenes.value.some(item => item.scene === route.query.scene)
  ? route.query.scene : scenes.value[0]?.scene)
const options = computed(() => scenes.value.map(item => ({ title: sceneName(item.scene), value: item.scene })))
const taskId = computed(() => /^[1-9][0-9]*$/.test(route.query.task || '') ? Number(route.query.task) : null)
const operator = ref(''), status = ref('all'), rows = ref([])
const tasks = useResource(async (more = false) => ({ more: more === true,
  ...(await tasksApi.list(scene.value, { status: status.value, offset: more === true ? tasks.data.value.next_offset : 0, limit: 20 })) }), { immediate: false })
watch(tasks.data, value => { if (value) rows.value = value.more ? [...rows.value, ...value.items] : value.items })
watch([scene, status], () => { rows.value = []; if (scene.value) tasks.reload() }, { immediate: true })
const selected = computed(() => rows.value.find(item => item.id === taskId.value))
function select(id) { router.push({ name: 'host-resources', query: { scene: scene.value, task: id || undefined } }) }
</script>

<template>
  <HostPage title="资源" description="浏览任务文件、查看产物，并保存独立交付。">
    <template #actions><v-select :model-value="scene" :items="options" label="场景" density="compact" class="scene"
      @update:model-value="value => router.push({ name: 'host-resources', query: { scene: value } })" /></template>
    <div v-if="scene" class="resources">
      <aside class="surface task-list">
        <v-btn :variant="taskId === null ? 'tonal' : 'text'" @click="select(null)">本场景共享资料</v-btn>
        <v-select v-model="status" :items="[{title:'全部任务',value:'all'},{title:'进行中',value:'active'},{title:'已完成',value:'done'},{title:'失败',value:'failed'},{title:'已取消',value:'cancelled'}]" density="compact" label="任务状态" hide-details />
        <ErrorNote v-if="tasks.error.value" title="读取任务失败" :error="tasks.error.value" />
        <v-list density="compact" nav>
          <v-list-item v-for="item in rows" :key="item.id" :active="taskId === item.id" @click="select(item.id)">
            <v-list-item-title class="goal">{{ item.goal }}</v-list-item-title>
            <v-list-item-subtitle>{{ taskStatus(item.status) }} · {{ formatTime(item.created) }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
        <v-btn v-if="tasks.data.value?.next_offset != null" variant="text" :loading="tasks.loading.value" @click="tasks.reload(true)">显示更多任务</v-btn>
      </aside>
      <div class="files">
        <div v-if="taskId" class="heading"><div><h2>{{ selected?.goal || `任务 #${taskId}` }}</h2>
          <RouterLink :to="{name:'host-tasks',query:{scene,id:taskId}}">查看任务与执行记录</RouterLink></div>
          <v-text-field v-model="operator" label="你的 QQ" inputmode="numeric" density="compact" hide-details class="operator" /></div>
        <ResourceBrowser :key="`${scene}:${taskId}`" :scene="scene" :task-id="taskId" :operator="operator" />
      </div>
    </div>
  </HostPage>
</template>

<style scoped>
.resources{display:grid;grid-template-columns:280px minmax(0,1fr);gap:16px;align-items:start}.task-list{display:grid;gap:10px}.files{min-width:0;display:grid;gap:12px}.heading{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}.heading h2,.goal{white-space:normal;overflow-wrap:anywhere}.operator{max-width:220px}.scene{min-width:200px}@media(max-width:850px){.resources{grid-template-columns:1fr}}
</style>
