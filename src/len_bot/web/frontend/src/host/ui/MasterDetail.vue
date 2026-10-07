<script setup>
import { mdiArrowLeft } from '@mdi/js'
defineProps({ selected: Boolean, defaultDetail: Boolean, listWidth: { type: String, default: '320px' }, backLabel: { type: String, default: '返回列表' } })
defineEmits(['back'])
</script>
<template>
  <div class="master-detail" :class="{ selected }" :style="{ '--list-width': listWidth }">
    <aside class="md-list"><slot name="list" /></aside>
    <div class="md-detail">
      <v-btn v-if="selected" class="md-back" variant="text" :prepend-icon="mdiArrowLeft" @click="$emit('back')">{{ backLabel }}</v-btn>
      <slot v-if="selected || defaultDetail" />
      <div v-else class="md-placeholder"><slot name="placeholder" /></div>
    </div>
  </div>
</template>
<style scoped>
.master-detail{display:grid;grid-template-columns:minmax(220px,var(--list-width)) minmax(0,1fr);gap:var(--sp-4);align-items:start;min-width:0}
.md-list{position:sticky;top:84px;max-height:calc(100vh - 108px);overflow:auto;min-width:0;border-radius:var(--radius-lg)}
.md-detail{display:grid;gap:var(--sp-4);min-width:0}
.md-back{display:none;justify-self:start}
.md-placeholder{border:1.5px dashed var(--line-strong);border-radius:var(--radius-lg);padding:var(--sp-6) var(--sp-4);text-align:center;color:var(--muted);background:var(--surface)}
.md-placeholder:empty{display:none}
@media(max-width:900px){
  .master-detail{grid-template-columns:minmax(0,1fr)}
  .md-list{position:static;max-height:none}
  .selected .md-list{display:none}
  .master-detail:not(.selected) .md-detail{display:none}
  .md-back{display:inline-flex}
}
</style>
