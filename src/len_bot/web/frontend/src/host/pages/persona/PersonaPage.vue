<script setup>
import { computed, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../ui/HostPage.vue'
import PageTabs from '../../ui/PageTabs.vue'
import ProfileTab from './ProfileTab.vue'
import KnowledgeTab from './KnowledgeTab.vue'
import StickersTab from './StickersTab.vue'
import PackageTab from './PackageTab.vue'

const route = useRoute()
const tabs = [['profile', '设定'], ['knowledge', '资料'], ['stickers', '表情'], ['package', '导入导出']]
const tab = computed(() => tabs.some(([key]) => key === route.query.tab) ? route.query.tab : 'profile')
const { scene, current } = useCurrentScene()
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage :title="current ? `角色 · ${current.persona.name}` : '角色'" :wide="tab === 'knowledge'">
    <PageTabs :tabs="tabs" :model-value="tab" label="角色内容" />
    <template v-if="scene">
      <ProfileTab v-if="tab === 'profile'" :key="`p${scene}`" :scene="scene" @dirty="value => dirty.profile = value" />
      <KnowledgeTab v-else-if="tab === 'knowledge'" :key="`k${scene}`" :scene="scene" @dirty="value => dirty.knowledge = value" />
      <StickersTab v-else-if="tab === 'stickers'" :key="`s${scene}`" :scene="scene" @dirty="value => dirty.stickers = value" />
      <PackageTab v-else :key="`f${scene}`" :scene="scene" @dirty="value => dirty.package = value" />
    </template>
  </HostPage>
</template>
