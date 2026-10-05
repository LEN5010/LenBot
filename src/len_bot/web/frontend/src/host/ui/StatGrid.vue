<script setup>
// Headline numbers. Items are { label, value, hint }.
defineProps({ items: { type: Array, required: true } })
</script>
<template>
  <div class="stat-grid">
    <div v-for="item in items" :key="item.label" class="stat">
      <span class="label">{{ item.label }}</span><strong>{{ item.value }}</strong>
      <span v-if="item.hint" class="hint">{{ item.hint }}</span>
    </div>
  </div>
</template>
<style scoped>
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,140px),1fr));gap:var(--sp-3)}
.stat{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);display:grid;gap:2px;min-width:0}
.label,.hint{font-size:var(--fs-sm);color:var(--muted)}
strong{font-size:var(--fs-2xl);font-weight:650;line-height:1.3;overflow-wrap:anywhere}
</style>
