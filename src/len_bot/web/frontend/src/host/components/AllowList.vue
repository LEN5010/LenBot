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
  <ul class="allow-list" :class="{ all: modelValue === 'all' }">
    <li v-for="item in items" :key="item.name">
      <v-checkbox :model-value="modelValue === 'all' || modelValue.includes(item.name)" :disabled="modelValue === 'all'"
        :label="item.label" hide-details density="compact" @update:model-value="value => toggle(item.name, value)" />
      <slot name="item" :item="item" />
      <p v-if="item.note" class="note">{{ item.note }}</p>
    </li>
    <li v-for="name in missing" :key="name">
      <v-checkbox :model-value="true" :label="`${name}（已不存在）`" hide-details density="compact" @update:model-value="toggle(name, false)" />
    </li>
  </ul>
</template>
<style scoped>
.allow-list{list-style:none;margin:var(--sp-2) 0 0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:0 var(--sp-5)}
.allow-list li{min-width:0;padding:var(--sp-1) 0 var(--sp-3);border-bottom:1px solid var(--line)}
.allow-list.all :deep(.v-selection-control--disabled){opacity:1}
.allow-list.all :deep(.v-selection-control--disabled .v-label){color:var(--ink)}
.allow-list .note{margin:var(--sp-1) 0 0 40px;font-size:var(--fs-sm);color:var(--warning)}
.allow-list :deep(.v-label){font-weight:600;opacity:1}
</style>
