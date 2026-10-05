<script setup>
import { initialField } from '../pluginConfig.js'

const props = defineProps({ field: { type: Object, required: true }, configured: Boolean })
const model = defineModel({ required: true })
function addRow() {
  model.value = [...model.value, Object.fromEntries(props.field.fields.map(child => [child.key, initialField(child)]))]
}
const removeRow = index => { model.value = model.value.filter((_, i) => i !== index) }
</script>

<template>
  <v-select v-if="field.options" v-model="model" :items="field.options" :label="field.key"
    :hint="field.description" persistent-hint />
  <v-switch v-else-if="field.type === 'boolean'" v-model="model" :label="field.description" />
  <v-textarea v-else-if="field.type === 'string_list'" v-model="model" rows="2" auto-grow :label="field.key"
    :hint="`${field.description} 每行一项。`" persistent-hint />
  <fieldset v-else-if="field.type === 'object_list' && field.fields.length" class="object-list">
    <legend>{{ field.description }}（{{ field.key }}）</legend>
    <div v-for="(row, index) in model" :key="index" class="object-row">
      <div class="row-heading"><strong>第 {{ index + 1 }} 项</strong>
        <v-btn size="small" variant="text" color="error" :aria-label="`删除 ${field.key} 第 ${index + 1} 项`" @click="removeRow(index)">删除</v-btn>
      </div>
      <PluginField v-for="child in field.fields" :key="child.key" :field="child" v-model="row[child.key]" />
    </div>
    <v-btn variant="tonal" size="small" :aria-label="`添加 ${field.key}`" @click="addRow">添加一项</v-btn>
  </fieldset>
  <v-textarea v-else-if="field.type === 'object_list'" v-model="model" rows="5" auto-grow class="mono"
    :label="field.key" :hint="field.description" persistent-hint />
  <v-text-field v-else v-model="model" :label="field.key"
    :type="field.type === 'secret' ? 'password' : ['integer', 'number'].includes(field.type) ? 'number' : 'text'"
    :min="field.minimum" :max="field.maximum" :step="field.type === 'integer' ? 1 : 'any'"
    :autocomplete="field.type === 'secret' ? 'off' : undefined" persistent-hint
    :hint="field.type === 'secret' && configured ? `${field.description} 已保存，留空不修改。` : field.description" />
</template>

<style scoped>
.object-list{border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3);min-width:0}
.object-list legend{padding:0 var(--sp-1);font-size:var(--fs-md)}
.object-row{display:grid;gap:var(--sp-4);padding:var(--sp-3) 0;border-bottom:1px solid var(--line);margin-bottom:var(--sp-3)}
.row-heading{display:flex;align-items:center;justify-content:space-between}
</style>
