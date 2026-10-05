<script setup>
import { mdiFlowerOutline } from '@mdi/js'
defineProps({ text: { type: String, required: true }, compact: Boolean, icon: { type: String, default: mdiFlowerOutline } })
</script>
<template>
  <div class="empty-state" :class="{ compact }">
    <span v-if="!compact" class="empty-icon"><v-icon :icon="icon" size="26" /></span>
    <p>{{ text }}</p><div v-if="$slots.default" class="empty-actions"><slot /></div>
  </div>
</template>
<style scoped>
.empty-state{padding:var(--sp-6) var(--sp-4);text-align:center;color:var(--muted);display:grid;gap:var(--sp-3);justify-items:center}
.empty-state.compact{padding:var(--sp-3) 0;text-align:left;justify-items:start}
.empty-icon{display:grid;place-items:center;width:52px;height:52px;border-radius:50%;background:var(--brand-soft);color:var(--brand);animation:rise var(--dur-3) var(--ease-out)}
.empty-state p{margin:0}
.empty-actions{display:flex;gap:var(--sp-2);flex-wrap:wrap}
</style>
