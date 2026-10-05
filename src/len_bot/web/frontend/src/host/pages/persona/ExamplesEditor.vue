<script setup>
// Operator-managed scenario/line references: the first eight, or up to eight with selected tags.
import { computed, watch } from 'vue'
import { mdiArrowUp, mdiClose, mdiPlus } from '@mdi/js'
import Panel from '../../ui/Panel.vue'

const examples = defineModel('examples', { type: Array, required: true })
const tags = defineModel('tags', { type: Array, required: true })
const available = computed(() => [...new Set(examples.value.flatMap(example => example.tags))])
watch(available, value => {
  if (tags.value.some(tag => !value.includes(tag))) tags.value = tags.value.filter(tag => value.includes(tag))
})
const add = () => examples.value.push({ context: '', line: '', tags: [] })
function up(index) {
  const [item] = examples.value.splice(index, 1)
  examples.value.splice(index - 1, 0, item)
}
</script>

<template>
  <Panel title="样例" description="写具体的情境和它在那时说的话，可以从聊天记录里挑原句。">
    <v-select v-if="available.length" v-model="tags" :items="available" label="只给它看带这些标签的样例" multiple chips closable-chips
      hint="不选就用前 8 条；选了就用带这些标签的，最多 8 条" persistent-hint />
    <p v-if="!examples.length" class="muted">还没有样例。</p>
    <ol class="plain-list list">
      <li v-for="(example, index) in examples" :key="index">
        <div class="fields">
          <v-textarea v-model="example.context" label="场合" rows="1" auto-grow />
          <v-textarea v-model="example.line" label="它说的话" rows="1" auto-grow />
          <v-combobox v-model="example.tags" :items="available" label="标签（可不填）" multiple chips closable-chips />
        </div>
        <div class="actions">
          <v-btn v-if="index" :icon="mdiArrowUp" variant="text" size="small" aria-label="上移" @click="up(index)" />
          <v-btn :icon="mdiClose" variant="text" size="small" aria-label="删除这条样例" @click="examples.splice(index, 1)" />
        </div>
      </li>
    </ol>
    <v-btn :prepend-icon="mdiPlus" variant="text" color="primary" class="add" @click="add">加一条样例</v-btn>
  </Panel>
</template>

<style scoped>
p{margin:0}
.list{display:grid;gap:var(--sp-3)}
.list li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:var(--sp-2);border:1px solid var(--line);border-radius:var(--radius);padding:var(--sp-3)}
.fields{display:grid;gap:var(--sp-2)}
.actions{display:flex;flex-direction:column}
.add{justify-self:start}
</style>
