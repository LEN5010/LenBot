<script setup>
import { computed } from 'vue'
import SchemaField from './SchemaField.vue'

const props = defineProps({
  fields: { type: Array, required: true },
  sceneChoices: { type: Array, required: true },
  configured: { type: Object, default: () => ({}) },
})
const values = defineModel({ type: Object, required: true })
const groups = computed(() => {
  const result = new Map()
  for (const field of props.fields) {
    const name = field.group || ''
    if (!result.has(name)) result.set(name, [])
    result.get(name).push(field)
  }
  return [...result].sort(([a], [b]) => (a !== '') - (b !== ''))
})
const undeclared = computed(() => props.fields.filter(field => field.type === 'object_list' && !field.fields.length))
</script>

<template>
  <div class="schema-form">
    <section v-for="[name, items] in groups" :key="name" class="schema-group">
      <h3 v-if="name">{{ name }}</h3>
      <SchemaField v-for="field in items" :key="field.key" v-model="values[field.key]" :field="field"
        :scene-choices="sceneChoices" :configured="Boolean(configured[field.key])" />
    </section>
    <p v-if="undeclared.length" class="muted small">
      {{ undeclared.map(field => field.label || field.key).join('、') }} 没有声明子字段，只能按 JSON 填写；插件作者可以在 plugin.toml 里补上 fields。
    </p>
  </div>
</template>

<style scoped>
.schema-form,.schema-group{display:grid;gap:var(--sp-4);min-width:0}
.schema-group+.schema-group{padding-top:var(--sp-3);border-top:1px solid var(--line)}
h3{font-size:var(--fs-md)}
</style>
