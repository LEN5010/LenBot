<script setup>
import { computed } from 'vue'
import { statusOf, toneColor } from '../status.js'
const props = defineProps({
  kind: { type: String, default: '' },
  value: { type: String, default: '' },
  text: { type: String, default: '' },
  tone: { type: String, default: '' },
  dot: Boolean,
  pulse: Boolean,
})
const shown = computed(() => {
  const known = props.kind ? statusOf(props.kind, props.value) : { text: '', tone: 'neutral' }
  return { text: props.text || known.text, tone: props.tone || known.tone }
})
</script>
<template>
  <span v-if="dot" class="status-dot" :class="shown.tone"><span class="dot" :class="{ pulse }" />{{ shown.text }}</span>
  <v-chip v-else :color="toneColor(shown.tone)" class="status-chip">{{ shown.text }}</v-chip>
</template>
<style scoped>
.status-dot{display:inline-flex;align-items:center;gap:6px;font-size:var(--fs-sm);color:var(--muted);white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--idle);flex:none}
.success .dot{background:var(--success)}
.warning .dot{background:var(--warning)}
.error .dot{background:var(--error)}
.info .dot{background:var(--info)}
.error{color:var(--error)}
.dot.pulse{color:var(--success);animation:pulse 2s var(--ease-out) infinite}
.status-chip{flex:none}
</style>
