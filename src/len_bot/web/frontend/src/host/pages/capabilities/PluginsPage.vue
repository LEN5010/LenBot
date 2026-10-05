<script setup>
import { computed, reactive } from 'vue'
import { useCurrentScene } from '../../../composables/useCurrentScene.js'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../ui/HostPage.vue'
import PluginsSection from './PluginsSection.vue'

const { scene } = useCurrentScene()
const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage title="插件" description="安装、配置插件，并选择在哪些群里启用。" wide>
    <PluginsSection v-if="scene" :key="scene" :scene="scene" @dirty="value => dirty.plugins = value" />
  </HostPage>
</template>
