<script setup>
defineProps({ title: { type: String, required: true }, description: { type: String, default: '' } })
</script>
<template>
  <div class="host-page">
    <header class="host-page-header">
      <div><h1>{{ title }}</h1><p v-if="description" class="muted">{{ description }}</p></div>
      <div class="host-page-actions"><slot name="actions" /></div>
    </header>
    <slot />
  </div>
</template>
<style scoped>
.host-page{max-width:1080px;margin-inline:auto;display:grid;gap:20px;min-width:0}
.host-page-header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.host-page-header h1{font-size:24px;margin:0}
.host-page-header p{margin:6px 0 0}
.host-page-actions{display:flex;gap:8px;flex-wrap:wrap}
.host-page :deep(.surface h2){font-size:17px;margin:0 0 4px}
.host-page :deep(.surface > .muted){margin-bottom:16px}
</style>
