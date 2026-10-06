<script setup>
// Sub-sections of a page. By default each tab is a link that sets `?tab=`
// and drops the object selected inside the old tab; `local` tabs only emit.
import { useRoute } from 'vue-router'
const props = defineProps({
  tabs: { type: Array, required: true },
  modelValue: { type: String, required: true },
  param: { type: String, default: 'tab' },
  clear: { type: Array, default: () => ['id', 'turn', 'task', 'path', 'scope', 'trial', 'item'] },
  local: Boolean,
  label: { type: String, default: '分类' },
})
const emit = defineEmits(['update:modelValue'])
const route = useRoute()
const items = () => props.tabs.map(tab => Array.isArray(tab) ? { value: tab[0], title: tab[1] } : tab)
function target(value) {
  const query = { ...route.query, [props.param]: value }
  for (const key of props.clear) delete query[key]
  return { name: route.name, query }
}
</script>
<template>
  <nav class="page-tabs" :aria-label="label">
    <template v-for="tab in items()" :key="tab.value">
      <button v-if="local" type="button" class="page-tab" :class="{ active: modelValue === tab.value }"
        :aria-pressed="modelValue === tab.value" @click="emit('update:modelValue', tab.value)">
        {{ tab.title }}<span v-if="tab.count" class="count">{{ tab.count }}</span></button>
      <RouterLink v-else :to="target(tab.value)" class="page-tab" :class="{ active: modelValue === tab.value }"
        :aria-current="modelValue === tab.value ? 'page' : undefined">
        {{ tab.title }}<span v-if="tab.count" class="count">{{ tab.count }}</span></RouterLink>
    </template>
  </nav>
</template>
<style scoped>
.page-tabs{display:inline-flex;gap:2px;max-width:100%;overflow-x:auto;padding:4px;border-radius:var(--radius);background:var(--track);scrollbar-width:none;justify-self:start}
.page-tab{flex:none;display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 var(--sp-4);border:0;border-radius:var(--radius-sm);background:none;
  color:var(--muted);font:inherit;font-size:var(--fs-md);cursor:pointer;white-space:nowrap;transition:background-color var(--dur-2) var(--ease-out),color var(--dur-1),box-shadow var(--dur-2) var(--ease-out)}
.page-tab:hover{color:var(--ink);text-decoration:none;background:rgb(255 255 255 / 55%)}
.page-tab.active{background:var(--surface);color:var(--primary);font-weight:650;box-shadow:var(--shadow-card)}
.count{min-width:18px;padding:0 5px;border-radius:9px;background:var(--track);color:var(--ink);font-size:var(--fs-xs);line-height:18px;text-align:center}
@media(max-width:600px){.page-tab{height:40px}}
</style>
