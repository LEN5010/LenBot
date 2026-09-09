<script setup>
import EntityLink from './EntityLink.vue'
defineProps({origin:{type:Object,default:null},name:{type:String,default:''},sceneId:{type:String,required:true}})
</script>

<template>
  <div v-if="origin" class="plugin-origin">
    <span>{{ name || origin.plugin_id }} · {{ origin.plugin_version }}</span>
    <span>入口 {{ origin.entry_id }}</span>
    <EntityLink type="event" :id="origin.source_event_id" :scene-id="sceneId" label="插件触发来源" />
    <span class="run-id">运行 {{ origin.run_id }}</span>
    <span v-if="origin.parent_run_id" class="run-id">父运行 {{ origin.parent_run_id }}</span>
    <span v-if="origin.parent_tool_call_id" class="run-id">父工具调用 {{ origin.parent_tool_call_id }}</span>
  </div>
</template>

<style scoped>
.plugin-origin{display:flex;align-items:center;flex-wrap:wrap;gap:8px 16px;margin:12px 0;color:rgb(var(--v-theme-on-surface-variant))}.run-id{overflow-wrap:anywhere;font-size:.85rem}
</style>
