<script setup>
import { computed } from 'vue'
const props = defineProps({
  modelValue: { type: [String, Array], required: true },
  items: { type: Array, required: true },
  allLabel: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue'])
const mode = computed({
  get: () => props.modelValue === 'all' ? 'all' : 'selected',
  set: value => emit('update:modelValue', value === 'all' ? 'all' : props.items.map(item => item.name)),
})
const missing = computed(() => props.modelValue === 'all' ? []
  : props.modelValue.filter(name => !props.items.some(item => item.name === name)))
function toggle(name, on) {
  emit('update:modelValue', on ? [...props.modelValue, name] : props.modelValue.filter(item => item !== name))
}
</script>
<template>
  <v-radio-group v-model="mode" hide-details inline>
    <v-radio :label="allLabel" value="all" />
    <v-radio label="只用勾选的" value="selected" />
  </v-radio-group>
  <ul class="allow-list">
    <li v-for="item in items" :key="item.name">
      <v-checkbox :model-value="modelValue === 'all' || modelValue.includes(item.name)" :disabled="modelValue === 'all'"
        :label="item.label" hide-details density="compact" @update:model-value="value => toggle(item.name, value)" />
      <p v-if="item.note" class="muted">{{ item.note }}</p>
      <slot name="item" :item="item" />
    </li>
    <li v-for="name in missing" :key="name">
      <v-checkbox :model-value="true" :label="`${name}（已不存在）`" hide-details density="compact" @update:model-value="toggle(name, false)" />
    </li>
  </ul>
</template>
<style scoped>
.allow-list{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,240px),1fr));gap:var(--sp-1) var(--sp-4)}
.allow-list li{min-width:0}
.allow-list p{margin:calc(-1 * var(--sp-1)) 0 var(--sp-2) 40px;font-size:var(--fs-sm)}
</style>
