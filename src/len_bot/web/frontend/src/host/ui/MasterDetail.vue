<script setup>
import { mdiArrowLeft } from '@mdi/js'
defineProps({ selected: Boolean, defaultDetail: Boolean, empty: Boolean, listWidth: { type: String, default: '320px' }, backLabel: { type: String, default: '返回列表' } })
defineEmits(['back'])
</script>
<template>
  <div class="master-detail" :class="{ selected, empty: empty && !selected && !defaultDetail }" :style="{ '--list-width': listWidth }">
    <aside class="md-list"><slot name="list" /></aside>
    <div class="md-detail">
      <v-btn v-if="selected" class="md-back" variant="text" :prepend-icon="mdiArrowLeft" @click="$emit('back')">{{ backLabel }}</v-btn>
      <slot v-if="selected || defaultDetail" />
      <div v-else class="md-placeholder"><slot name="placeholder" /></div>
    </div>
  </div>
</template>
<style scoped>
.master-detail{display:grid;grid-template-columns:minmax(220px,var(--list-width)) minmax(0,1fr);gap:var(--sp-6);align-items:start;min-width:0}
.md-list{position:sticky;top:88px;max-height:calc(100vh - 112px);overflow:auto;min-width:0;margin-left:calc(-1 * var(--sp-2));padding-right:var(--sp-4);border-right:1px solid var(--line)}
.md-detail{display:grid;gap:var(--sp-4);min-width:0}
.selected .md-detail{animation:rise var(--dur-3) var(--ease-out)}
.md-back{display:none;justify-self:start}
.md-placeholder{padding:var(--sp-2) 0;color:var(--muted);max-width:520px}
.md-placeholder:empty{display:none}
.master-detail.empty{grid-template-columns:minmax(0,640px)}
.master-detail.empty .md-list{position:static;max-height:none;border-right:0;padding-right:0}
.master-detail.empty .md-detail{display:none}
@media(max-width:900px){
  .master-detail{grid-template-columns:minmax(0,1fr)}
  .md-list{position:static;max-height:none;margin-left:0;padding-right:0;border-right:0}
  .selected .md-list{display:none}
  .master-detail:not(.selected) .md-detail{display:none}
  .md-back{display:inline-flex}
}
</style>
