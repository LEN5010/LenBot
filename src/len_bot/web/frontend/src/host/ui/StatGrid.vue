<script setup>
// Headline numbers. Items are { label, value, hint, icon, to }.
defineProps({ items: { type: Array, required: true } })
</script>
<template>
  <div class="stat-grid">
    <component :is="item.to ? 'RouterLink' : 'div'" v-for="(item, index) in items" :key="item.label" :to="item.to"
      class="stat rise" :class="{ link: item.to }" :style="{ '--i': index }">
      <span v-if="item.icon" class="stat-icon"><v-icon :icon="item.icon" size="20" /></span>
      <span class="label">{{ item.label }}</span><strong>{{ item.value }}</strong>
      <span v-if="item.hint" class="hint">{{ item.hint }}</span>
    </component>
  </div>
</template>
<style scoped>
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:var(--sp-3)}
.stat{position:relative;border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-4);display:grid;gap:2px;min-width:0;background:var(--surface);color:inherit;
  transition:transform var(--dur-2) var(--ease-out),box-shadow var(--dur-2) var(--ease-out),border-color var(--dur-1)}
.stat:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:var(--line-strong);text-decoration:none}
.stat-icon{position:absolute;top:var(--sp-3);right:var(--sp-3);display:grid;place-items:center;width:34px;height:34px;border-radius:var(--radius-sm);background:var(--brand-soft);color:var(--primary)}
.label,.hint{font-size:var(--fs-sm);color:var(--muted)}
strong{font-size:var(--fs-2xl);font-weight:700;line-height:1.3;overflow-wrap:anywhere;letter-spacing:-.02em}
</style>
