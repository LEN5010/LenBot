<script setup>
import { computed, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import { host } from '../../store.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import OperatorField from '../../components/OperatorField.vue'
import TasksTab from './TasksTab.vue'
import SchedulesTab from './SchedulesTab.vue'
import ProactiveTab from './ProactiveTab.vue'

const route = useRoute()
const { scene } = useCurrentScene()
const tabs = computed(() => [['tasks', '独立任务'], ['schedules', '提醒'], ...(scene.value?.split(':', 3)[1] === 'group' ? [['proactive', '主动开话题']] : [])])
const tab = computed(() => tabs.value.some(([key]) => key === route.query.tab) ? route.query.tab : 'tasks')
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { keep: ['id'], onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage title="任务" description="Bot 接下的独立任务、提醒和主动开话题。" :wide="tab === 'tasks'">
    <template v-if="tab !== 'proactive'" #actions><OperatorField /></template>
    <PageTabs :tabs="tabs" :model-value="tab" label="任务" />
    <template v-if="scene">
      <TasksTab v-if="tab === 'tasks'" :key="`t${scene}`" :scene="scene" :operator="host.operator" @dirty="value => dirty.tasks = value" />
      <SchedulesTab v-else-if="tab === 'schedules'" :key="`s${scene}`" :scene="scene" :operator="host.operator" @dirty="value => dirty.schedules = value" />
      <ProactiveTab v-else :key="`p${scene}`" :scene="scene" />
    </template>
  </HostPage>
</template>
