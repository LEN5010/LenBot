<script setup>
import { computed } from 'vue'
import { mdiPlus } from '@mdi/js'
import { sceneName } from '../../api.js'
import { initialField } from '../pluginConfig.js'
import FieldInfo from '../ui/FieldInfo.vue'

const props = defineProps({
  field: { type: Object, required: true },
  sceneChoices: { type: Array, required: true },
  configured: Boolean,
})
const model = defineModel({ required: true })
const title = computed(() => props.field.label || props.field.key)
const secretKept = computed(() => props.field.type === 'secret' && props.configured)
const scenes = computed(() => props.sceneChoices.map(scene => ({ title: sceneName(scene), value: scene })))
const inputType = computed(() => props.field.type === 'secret' ? 'password'
  : ['integer', 'number'].includes(props.field.type) ? 'number' : props.field.type === 'url' ? 'url' : 'text')

function addRow() {
  model.value = [...model.value, Object.fromEntries(props.field.fields.map(child => [child.key, initialField(child)]))]
}
const removeRow = index => { model.value = model.value.filter((_, i) => i !== index) }
function summary(row) {
  const parts = props.field.fields.map(child => {
    const value = row[child.key]
    if (child.type === 'scene_list') return value.length ? value.map(sceneName).join('、') : ''
    if (child.type === 'scene') return value ? sceneName(value) : ''
    if (child.options) return child.options.find(option => option.value === value)?.label ?? ''
    return typeof value === 'boolean' ? '' : String(value ?? '').split('\n')[0]
  }).filter(Boolean)
  return parts.slice(0, 3).join(' · ')
}
</script>

<template>
  <v-select v-if="field.options" v-model="model" :items="field.options" item-title="label" item-value="value"
    :label="title"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-select>
  <v-switch v-else-if="field.type === 'boolean'" v-model="model" :label="title"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-switch>
  <v-select v-else-if="field.type === 'scene'" v-model="model" :items="scenes" :label="title"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-select>
  <v-select v-else-if="field.type === 'scene_list'" v-model="model" :items="scenes" :label="title" multiple chips closable-chips><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-select>
  <v-textarea v-else-if="field.type === 'string_list'" v-model="model" rows="2" auto-grow :label="title"
    placeholder="每行一项"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-textarea>
  <v-textarea v-else-if="field.type === 'string' && field.multiline" v-model="model" rows="3" auto-grow :label="title"
    :placeholder="field.placeholder ?? undefined"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-textarea>
  <fieldset v-else-if="field.type === 'object_list' && field.fields.length" class="rows">
    <legend>{{ title }} <FieldInfo v-if="field.description" :text="field.description" /></legend>
    <v-card v-for="(row, index) in model" :key="index" variant="outlined" class="row-card">
      <div class="row-heading">
        <strong>{{ summary(row) || `第 ${index + 1} 项` }}</strong>
        <v-btn size="small" variant="text" color="error" :aria-label="`删除 ${title} 第 ${index + 1} 项`" @click="removeRow(index)">删除</v-btn>
      </div>
      <div class="row-fields">
        <SchemaField v-for="child in field.fields" :key="child.key" v-model="row[child.key]" :field="child" :scene-choices="sceneChoices" />
      </div>
    </v-card>
    <v-btn variant="text" color="primary" size="small" :prepend-icon="mdiPlus" @click="addRow">添加{{ title }}</v-btn>
  </fieldset>
  <v-textarea v-else-if="field.type === 'object_list'" v-model="model" rows="5" auto-grow class="mono"
    :label="`${title}（JSON 对象列表）`"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-textarea>
  <v-text-field v-else v-model="model" :label="title" :type="inputType" :placeholder="secretKept ? '已保存，留空不修改' : field.placeholder ?? undefined"
    :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :step="field.type === 'integer' ? 1 : 'any'"
    :autocomplete="field.type === 'secret' ? 'off' : undefined"><template v-if="field.description" #append><FieldInfo :text="field.description" /></template></v-text-field>
</template>

<style scoped>
.rows{border:0;padding:0;margin:0;display:grid;gap:var(--sp-3);min-width:0}
.rows legend{font-size:var(--fs-md);font-weight:600;padding:0}
.row-card{padding:var(--sp-3);display:grid;gap:var(--sp-3)}
.row-heading{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-2);min-width:0}
.row-heading strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row-fields{display:grid;gap:var(--sp-4)}
</style>
