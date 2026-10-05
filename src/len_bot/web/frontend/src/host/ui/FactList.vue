<script setup>
// Label and value pairs. Items are [label, value] or { label, value }; empty
// values are skipped.
defineProps({ items: { type: Array, default: () => [] } })
const pair = item => Array.isArray(item) ? { label: item[0], value: item[1] } : item
</script>
<template>
  <dl class="fact-list">
    <template v-for="item in items.map(pair).filter(item => item.value !== '' && item.value !== null && item.value !== undefined)" :key="item.label">
      <div><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div>
    </template>
    <slot />
  </dl>
</template>
<style scoped>
.fact-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,180px),1fr));gap:var(--sp-3) var(--sp-4);margin:0}
.fact-list :deep(dt){font-size:var(--fs-xs);color:var(--muted)}
.fact-list :deep(dd){margin:2px 0 0;overflow-wrap:anywhere}
</style>
