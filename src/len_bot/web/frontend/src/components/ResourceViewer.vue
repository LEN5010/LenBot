<script setup>
import { computed } from 'vue'
const props=defineProps({ title:{type:String,default:'原始资料'},content:[String,Object,Array],loading:Boolean,error:String })
const text=computed(()=>typeof props.content==='string'?props.content:JSON.stringify(props.content,null,2))
</script>
<template>
  <section class="resource-viewer" :aria-label="title">
    <div class="resource-title"><h3>{{ title }}</h3><slot name="actions" /></div>
    <v-progress-linear v-if="loading" indeterminate color="primary" />
    <v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
    <pre v-if="content !== null && content !== undefined" tabindex="0">{{ text }}</pre>
    <p v-else-if="!loading && !error" class="muted">暂无可读取内容</p>
    <slot />
  </section>
</template>
<style scoped>
.resource-viewer{min-width:0}.resource-title{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.resource-title h3{font-size:15px}.resource-viewer pre{padding:16px;background:#f6f8fb;border:1px solid var(--line);border-radius:8px;max-height:65vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.75 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--ink)}
</style>
