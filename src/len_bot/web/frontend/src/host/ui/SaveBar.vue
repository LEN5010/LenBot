<script setup>
// Sticky save bar for a page-sized form that is saved in one go.
import { nextTick } from 'vue'
import { openRestart } from '../restart.js'
import ErrorNote from './ErrorNote.vue'
const props = defineProps({
  onSave: { type: Function, required: true },
  dirty: { type: Boolean, required: true },
  saving: { type: Boolean, default: false },
  error: { type: [Object, String], default: null },
  problem: { type: String, default: '' },
  label: { type: String, default: '保存' },
  restart: { type: Boolean, default: true },
})
defineEmits(['discard'])
async function saveRestart() {
  await props.onSave()
  await nextTick()
  if (!props.error && !props.dirty) await openRestart()
}
</script>
<template>
  <div class="save-bar" :class="{ show: dirty || error }">
    <ErrorNote v-if="error" title="没有保存成功" :error="error" />
    <div class="save-row">
      <span class="hint" :class="{ problem }">{{ problem || (dirty ? '有未保存的修改' : '') }}</span>
      <v-btn variant="text" :disabled="!dirty || saving" @click="$emit('discard')">放弃修改</v-btn>
      <v-btn v-if="restart" type="button" variant="outlined" :disabled="!dirty || saving || Boolean(problem)" @click="saveRestart">保存并重启</v-btn>
      <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || Boolean(problem)">{{ label }}</v-btn>
    </div>
  </div>
</template>
<style scoped>
.save-bar{position:sticky;bottom:var(--sp-4);z-index:3;display:none;gap:var(--sp-2);background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:var(--sp-3) var(--sp-4);box-shadow:var(--shadow-float)}
.save-bar.show{display:grid;animation:rise var(--dur-2) var(--ease-out)}
.save-row{display:flex;align-items:center;justify-content:flex-end;gap:var(--sp-2);flex-wrap:wrap}
.hint{margin-right:auto;color:var(--muted);font-size:var(--fs-sm)}
.hint.problem{color:var(--error)}
</style>
