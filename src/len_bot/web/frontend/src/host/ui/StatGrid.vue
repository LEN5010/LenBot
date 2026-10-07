<script setup>
import Sparkline from './Sparkline.vue'
defineProps({ items: { type: Array, required: true } })
const moving = series => Array.isArray(series) && series.some(value => value > 0)
</script>
<template>
  <div class="stat-grid" :style="{ '--count': items.length }">
    <component :is="item.to ? 'RouterLink' : 'div'" v-for="item in items" :key="item.label" :to="item.to"
      class="stat" :class="{ link: item.to }">
      <span class="label">{{ item.label }}</span>
      <strong>{{ item.value }}</strong>
      <span v-if="item.hint" class="hint">{{ item.hint }}</span>
      <Sparkline v-if="moving(item.series)" :values="item.series" :label="item.seriesLabel || `${item.label}走势`" class="trend" />
    </component>
  </div>
</template>
<style scoped>
.stat-grid{display:grid;grid-template-columns:repeat(var(--count),minmax(0,1fr));border-block:1px solid var(--line)}
.stat{position:relative;display:grid;gap:2px;align-content:start;min-width:0;padding:var(--sp-4) var(--sp-5);border-left:1px solid var(--line);color:inherit;
  animation:rise var(--dur-4) var(--ease-out) both;transition:background-color var(--dur-2) var(--ease-out)}
.stat:first-child{border-left:0;padding-left:0}
.stat:nth-child(2){animation-delay:40ms}.stat:nth-child(3){animation-delay:80ms}.stat:nth-child(4){animation-delay:120ms}.stat:nth-child(5){animation-delay:160ms}
.stat.link:hover{text-decoration:none}
@media(hover:hover){.stat.link:hover strong{color:var(--primary)}}
.label,.hint{font-size:var(--fs-sm);color:var(--muted)}
strong{font-size:28px;font-weight:650;line-height:1.25;overflow-wrap:anywhere;letter-spacing:-.02em;font-variant-numeric:tabular-nums;transition:color var(--dur-2)}
.trend{margin-top:var(--sp-2)}
@media(max-width:800px){
  .stat-grid{grid-template-columns:1fr 1fr;border-bottom:0}
  .stat,.stat:first-child{border-left:0;padding:var(--sp-3) 0;border-bottom:1px solid var(--line)}
  .stat:nth-child(even){padding-left:var(--sp-4);border-left:1px solid var(--line)}
  .stat:nth-child(odd):last-child{grid-column:1 / -1}
}
</style>
