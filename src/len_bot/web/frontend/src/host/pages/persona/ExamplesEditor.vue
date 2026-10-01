<script setup>
// Hand-written sample lines. Up to eight go to the model: the first eight, or the ones with the chosen tags.
import { computed, watch } from 'vue'
import { mdiArrowUp, mdiClose, mdiPlus } from '@mdi/js'

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
  <section class="surface examples">
    <h2>样例</h2>
    <p class="muted">写几句它在某种场合会说的话，模型会照着这个感觉说。</p>
    <v-select v-if="available.length" v-model="tags" :items="available" label="只给它看带这些标签的样例" multiple chips closable-chips
      hint="不选就用前 8 条；选了就用带这些标签的，最多 8 条" persistent-hint />
    <p v-if="!examples.length" class="muted">还没有样例。</p>
    <ol>
      <li v-for="(example, index) in examples" :key="index">
        <div class="fields">
          <v-textarea v-model="example.context" label="场合" rows="1" auto-grow density="compact" hide-details />
          <v-textarea v-model="example.line" label="它说的话" rows="1" auto-grow density="compact" hide-details />
          <v-combobox v-model="example.tags" :items="available" label="标签（可不填）" multiple chips closable-chips density="compact" hide-details />
        </div>
        <div class="actions">
          <v-btn v-if="index" :icon="mdiArrowUp" variant="text" size="small" aria-label="上移" @click="up(index)" />
          <v-btn :icon="mdiClose" variant="text" size="small" aria-label="删除这条样例" @click="examples.splice(index, 1)" />
        </div>
      </li>
    </ol>
    <div><v-btn :prepend-icon="mdiPlus" variant="text" @click="add">加一条样例</v-btn></div>
  </section>
</template>

<style scoped>
.examples{display:grid;gap:12px}
.examples p{margin:0}
ol{list-style:none;margin:0;padding:0;display:grid;gap:12px}
li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border:1px solid var(--line);border-radius:10px;padding:12px}
.fields{display:grid;gap:8px}
.actions{display:flex;flex-direction:column}
</style>
