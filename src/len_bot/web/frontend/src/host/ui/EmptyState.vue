<script setup>
defineProps({ text: { type: String, required: true }, compact: Boolean })
</script>
<template>
  <div class="empty-state" :class="{ compact }"><p>{{ text }}</p><div v-if="$slots.default" class="empty-actions"><slot /></div></div>
</template>
<style scoped>
.empty-state{padding:var(--sp-6) var(--sp-4);text-align:center;color:var(--muted);display:grid;gap:var(--sp-3);justify-items:center}
.empty-state.compact{padding:var(--sp-3) 0;text-align:left;justify-items:start}
.empty-state p{margin:0}
.empty-actions{display:flex;gap:var(--sp-2);flex-wrap:wrap}
</style>
