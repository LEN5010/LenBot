<script setup>
// Rows the user adds and removes. The slot renders one row's fields; `make`
// creates a new row; without `addLabel` rows can only be removed. The array is edited in place.
import { mdiClose, mdiPlus } from '@mdi/js'
const props = defineProps({
  items: { type: Array, required: true },
  make: { type: Function, required: true },
  addLabel: { type: String, default: '添加' },
  columns: { type: String, default: '' },
  emptyText: { type: String, default: '' },
  disabled: Boolean,
})
function add() { props.items.push(props.make()) }
</script>
<template>
  <div class="row-editor">
    <p v-if="!items.length && emptyText" class="muted empty">{{ emptyText }}</p>
    <div v-for="(item, index) in items" :key="index" class="editor-row">
      <div class="editor-fields" :style="columns ? { '--columns': columns } : undefined"><slot :item="item" :index="index" /></div>
      <v-btn :icon="mdiClose" variant="text" size="small" aria-label="删除这一行" :disabled="disabled" @click="items.splice(index, 1)" />
    </div>
    <v-btn v-if="addLabel" class="add" variant="text" color="primary" :prepend-icon="mdiPlus" :disabled="disabled" @click="add">{{ addLabel }}</v-btn>
  </div>
</template>
<style scoped>
.row-editor{display:grid;gap:var(--sp-2);min-width:0}
.empty{margin:0}
.editor-row{display:flex;align-items:flex-start;gap:var(--sp-2);min-width:0}
.editor-fields{flex:1;min-width:0;display:grid;grid-template-columns:var(--columns,repeat(auto-fit,minmax(min(100%,160px),1fr)));gap:var(--sp-3);align-items:start}
.add{justify-self:start}
@media(max-width:700px){.editor-fields{grid-template-columns:1fr}}
</style>
