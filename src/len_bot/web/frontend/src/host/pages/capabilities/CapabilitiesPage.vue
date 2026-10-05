<script setup>
import { computed, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import ToolsSection from './ToolsSection.vue'
import SkillsSection from './SkillsSection.vue'
import McpSection from './McpSection.vue'
import WebServicesSection from './WebServicesSection.vue'
import WorkerSection from './WorkerSection.vue'
import BrowserSection from './BrowserSection.vue'

const route = useRoute()
const tabs = [['tools', '工具与技能'], ['mcp', 'MCP 服务'], ['web', '网页'], ['tasks', '独立任务']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'tools')
const { scene } = useCurrentScene()
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage title="能力" description="Bot 在群里能用的工具、技能和外部服务。" :wide="tab === 'mcp'">
    <PageTabs :tabs="tabs" :model-value="tab" label="能力分类" :clear="['item']" />
    <template v-if="tab === 'tools' && scene">
      <ToolsSection :key="`t${scene}`" :scene="scene" @dirty="value => dirty.tools = value" />
      <SkillsSection :key="`s${scene}`" :scene="scene" @dirty="value => dirty.skills = value" />
    </template>
    <McpSection v-else-if="tab === 'mcp'" @dirty="value => dirty.mcp = value" />
    <WebServicesSection v-else-if="tab === 'web'" @dirty="value => dirty.web = value" />
    <template v-else-if="tab === 'tasks'">
      <WorkerSection @dirty="value => dirty.worker = value" />
      <BrowserSection @dirty="value => dirty.browser = value" />
    </template>
  </HostPage>
</template>
