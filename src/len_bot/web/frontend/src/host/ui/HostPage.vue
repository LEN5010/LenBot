<script setup>
// Page frame: title, one-line description, page actions. Workbench pages
// (list beside detail) use `wide`.
defineProps({ title: { type: String, required: true }, description: { type: String, default: '' }, wide: Boolean })
</script>
<template>
  <div class="host-page" :class="{ wide }">
    <header class="host-page-header">
      <div class="host-page-title"><h1>{{ title }}</h1><p v-if="description" class="muted">{{ description }}</p></div>
      <div v-if="$slots.actions" class="host-page-actions"><slot name="actions" /></div>
    </header>
    <slot />
  </div>
</template>
<style scoped>
.host-page{max-width:1080px;margin-inline:auto;display:grid;gap:var(--sp-4);min-width:0}
.host-page.wide{max-width:1440px}
.host-page-header{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--sp-4);flex-wrap:wrap;margin-bottom:var(--sp-1)}
.host-page-title{min-width:0}
.host-page-title p{margin:var(--sp-1) 0 0}
.host-page-actions{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
</style>
