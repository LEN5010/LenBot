<script setup>
// Per-scene overrides edited as rows instead of a JSON object.
import { computed } from 'vue'
import { sceneName } from '../../api.js'
const props = defineProps({
  modelValue: { type: Object, required: true },
  scenes: { type: Array, required: true },
  valueLabel: { type: String, required: true },
  type: { type: String, default: 'number' },
  emptyValue: { default: null },
  addLabel: { type: String, default: '添加群' },
})
const emit = defineEmits(['update:modelValue'])
const rows = computed(() => Object.entries(props.modelValue))
const unused = computed(() => props.scenes.filter(scene => !Object.hasOwn(props.modelValue, scene)))
function set(scene, value) {
  emit('update:modelValue', { ...props.modelValue, [scene]: value })
}
function remove(scene) {
  const { [scene]: _, ...rest } = props.modelValue
  emit('update:modelValue', rest)
}
function convert(value) {
  if (value === '' || value === null) return props.emptyValue
  return props.type === 'number' ? Number(value) : value
}
</script>
<template>
  <div class="scene-rows">
    <div v-for="[scene, value] in rows" :key="scene" class="scene-row">
      <span class="scene-row-name">{{ sceneName(scene) }}</span>
      <v-text-field :model-value="value ?? ''" :type="type === 'number' ? 'number' : 'text'" :label="valueLabel"
        @update:model-value="item => set(scene, convert(item))" />
      <v-btn variant="text" @click="remove(scene)">移除</v-btn>
    </div>
    <v-menu v-if="unused.length">
      <template #activator="{ props: menu }"><v-btn v-bind="menu" variant="outlined" size="small">{{ addLabel }}</v-btn></template>
      <v-list density="compact"><v-list-item v-for="scene in unused" :key="scene" :title="sceneName(scene)"
        @click="set(scene, emptyValue)" /></v-list>
    </v-menu>
  </div>
</template>
<style scoped>
.scene-rows{display:grid;gap:8px;justify-items:start}
.scene-row{display:grid;grid-template-columns:minmax(120px,180px) minmax(0,240px) auto;gap:12px;align-items:center;width:100%}
.scene-row-name{font-weight:600}
@media(max-width:600px){.scene-row{grid-template-columns:1fr auto}.scene-row .v-input{grid-column:1/-1;grid-row:2}}
</style>
