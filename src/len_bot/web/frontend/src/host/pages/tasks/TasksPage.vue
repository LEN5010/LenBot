<script setup>
import { computed, reactive, ref } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { sceneName } from '../../../api.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import HostPage from '../../components/HostPage.vue'
import TasksTab from './TasksTab.vue'
import SchedulesTab from './SchedulesTab.vue'
import ProactiveTab from './ProactiveTab.vue'

const route = useRoute(), router = useRouter()
const scenes = computed(() => host.state?.scenes || [])
const scene = computed(() => typeof route.query.scene === 'string' && scenes.value.some(item => item.scene === route.query.scene)
  ? route.query.scene : scenes.value[0]?.scene)
const tabs = computed(() => [['tasks', '独立任务'], ['schedules', '提醒'], ...(scene.value?.startsWith('group:') ? [['proactive', '主动开话题']] : [])])
const tab = computed(() => tabs.value.some(([key]) => key === route.query.tab) ? route.query.tab : 'tasks')
const options = computed(() => scenes.value.map(item => ({ title: sceneName(item.scene), value: item.scene })))
const target = query => ({ name: 'host-tasks', query: { ...route.query, ...query } })
// Permissions for tasks and reminders follow a real QQ, so the person using the panel says which one they are.
const operator = ref('')

const dirty = reactive({})
const { confirmLeave } = useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)))
onBeforeRouteUpdate((to, from) => {
  if (to.query.scene === from.query.scene && to.query.tab === from.query.tab) return true
  if (!confirmLeave()) return false
  for (const key of Object.keys(dirty)) dirty[key] = false
  return true
})
</script>

<template>
  <HostPage title="任务" description="Bot 接下的独立任务、提醒和主动开话题。">
    <template v-if="options.length" #actions>
      <v-select :model-value="scene" :items="options" label="群聊" density="compact" class="scene-switch"
        @update:model-value="value => router.push({ name: 'host-tasks', query: { scene: value, tab } })" />
    </template>
    <div class="bar">
      <nav class="task-tabs" aria-label="任务">
        <v-btn v-for="[key, label] in tabs" :key="key" :to="target({ tab: key, id: undefined })" :active="tab === key"
          :variant="tab === key ? 'tonal' : 'text'" :color="tab === key ? 'primary' : undefined"
          :aria-current="tab === key ? 'page' : undefined">{{ label }}</v-btn>
      </nav>
      <v-text-field v-if="tab !== 'proactive'" v-model="operator" label="你的 QQ" inputmode="numeric" density="compact"
        hint="新建、回答、取消时按这个 QQ 判断权限" persistent-hint class="operator" />
    </div>
    <template v-if="scene">
      <TasksTab v-if="tab === 'tasks'" :key="`t${scene}`" :scene="scene" :operator="operator" @dirty="value => dirty.tasks = value" />
      <SchedulesTab v-else-if="tab === 'schedules'" :key="`s${scene}`" :scene="scene" :operator="operator" @dirty="value => dirty.schedules = value" />
      <ProactiveTab v-else :key="`p${scene}`" :scene="scene" />
    </template>
  </HostPage>
</template>

<style scoped>
.bar{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:8px}
.task-tabs{display:flex;gap:4px;flex-wrap:wrap}
.operator{max-width:260px;min-width:200px}
.scene-switch{min-width:200px}
</style>
