<script setup>
import { computed, reactive } from 'vue'
import { useUnsavedChanges } from '../../../composables/useUnsavedChanges.js'
import HostPage from '../../ui/HostPage.vue'
import PluginsSection from './PluginsSection.vue'

const dirty = reactive({})
useUnsavedChanges(computed(() => Object.values(dirty).some(Boolean)), { onDiscard: () => { for (const key of Object.keys(dirty)) dirty[key] = false } })
</script>

<template>
  <HostPage title="插件" description="安装、配置插件，并选择在哪些群里启用。" wide>
    <PluginsSection @dirty="value => dirty.plugins = value" />
  </HostPage>
</template>
