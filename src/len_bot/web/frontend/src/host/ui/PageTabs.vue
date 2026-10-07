<script setup>
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
.page-tabs{display:flex;gap:var(--sp-5);max-width:100%;overflow-x:auto;border-bottom:1px solid var(--line);scrollbar-width:none}
.page-tab{position:relative;flex:none;display:inline-flex;align-items:center;gap:6px;height:40px;padding:0 2px;border:0;background:none;
  color:var(--muted);font:inherit;font-size:var(--fs-md);cursor:pointer;white-space:nowrap;transition:color var(--dur-2) var(--ease-out)}
.page-tab::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;border-radius:2px 2px 0 0;background:var(--brand);transform:scaleX(0);transition:transform var(--dur-3) var(--ease-out)}
.page-tab:hover{color:var(--ink);text-decoration:none}
.page-tab.active{color:var(--ink);font-weight:600}
.page-tab.active::after{transform:scaleX(1)}
.count{min-width:18px;padding:0 5px;border-radius:9px;background:var(--fill);color:var(--ink);font-size:var(--fs-xs);line-height:18px;text-align:center}
@media(max-width:600px){.page-tab{height:44px}}
</style>
