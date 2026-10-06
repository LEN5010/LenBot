<script setup>
// Headline numbers. Items are { label, value, hint, icon, to, series, seriesLabel };
// `series` draws a small trend line under the number.
import Sparkline from './Sparkline.vue'
defineProps({ items: { type: Array, required: true } })
</script>
<template>
  <div class="stat-grid">
    <component :is="item.to ? 'RouterLink' : 'div'" v-for="item in items" :key="item.label" :to="item.to"
      class="stat" :class="{ link: item.to }">
      <span class="stat-head"><span class="label">{{ item.label }}</span><v-icon v-if="item.icon" :icon="item.icon" size="18" class="stat-icon" /></span>
      <strong>{{ item.value }}</strong>
      <span v-if="item.hint" class="hint">{{ item.hint }}</span>
      <Sparkline v-if="item.series" :values="item.series" :label="item.seriesLabel || `${item.label}走势`" class="trend" />
    </component>
  </div>
</template>
<style scoped>
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:var(--sp-3)}
.stat{border:1px solid var(--line);border-radius:var(--radius-lg);padding:var(--sp-4);display:grid;gap:2px;min-width:0;background:var(--surface);color:inherit;
  align-content:start;transition:border-color var(--dur-1),box-shadow var(--dur-1)}
.stat.link:hover{border-color:var(--line-strong);box-shadow:var(--shadow-hover);text-decoration:none}
.stat-head{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2)}
.stat-icon{color:var(--muted)}
.label,.hint{font-size:var(--fs-sm);color:var(--muted)}
strong{font-size:var(--fs-2xl);font-weight:650;line-height:1.3;overflow-wrap:anywhere;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.trend{margin-top:var(--sp-2)}
</style>
