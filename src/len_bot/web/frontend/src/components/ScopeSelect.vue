<script setup>
import { computed, onMounted } from 'vue'
import { useAppState, loadScopes } from '../composables/useAppState.js'
import { sceneName } from '../api.js'
const props = defineProps({ modelValue:{type:String,default:''}, label:{type:String,default:'场景范围'}, includeGlobal:Boolean, clearable:{type:Boolean,default:true}, disabled:Boolean })
const emit = defineEmits(['update:modelValue'])
const app = useAppState()
onMounted(() => loadScopes())
const items = computed(() => [ ...(props.includeGlobal ? [{title:'公共范围 · global-safe',value:'global-safe'}] : []), ...app.scenes.map(scene=>({title:scene.display_name || sceneName(scene.scene_id),value:scene.scene_id})) ])
</script>
<template>
  <v-select class="scope-select" :model-value="modelValue || null" :items="items" :label="label" :clearable="clearable" :disabled="disabled"
    :error-messages="app.sceneError" :loading="!app.loadedScenes && !app.sceneError" @update:model-value="emit('update:modelValue', $event || '')" />
</template>
<style scoped>.scope-select { min-width:0;width:260px;max-width:100% }</style>
