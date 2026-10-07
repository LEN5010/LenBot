<script setup>
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
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:var(--sp-3)}
.stat{border-radius:var(--radius-lg);padding:var(--sp-4) var(--sp-4) var(--sp-3);display:grid;gap:2px;min-width:0;background:var(--fill);color:inherit;
  align-content:start;transition:background-color var(--dur-2) var(--ease-out),transform var(--dur-2) var(--ease-out);animation:rise var(--dur-4) var(--ease-out) both}
.stat:nth-child(2){animation-delay:40ms}.stat:nth-child(3){animation-delay:80ms}.stat:nth-child(4){animation-delay:120ms}.stat:nth-child(5){animation-delay:160ms}
.stat.link:hover{background:var(--track);text-decoration:none;transform:translateY(-1px)}
.stat-head{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2)}
.stat-icon{color:var(--muted)}
.label,.hint{font-size:var(--fs-sm);color:var(--muted)}
strong{font-size:28px;font-weight:650;line-height:1.25;overflow-wrap:anywhere;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.trend{margin-top:var(--sp-2)}
</style>
