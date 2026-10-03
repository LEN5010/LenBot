<script setup>
// One independently saved block of settings.
import { nextTick } from 'vue'
import { openRestart } from '../restart.js'
import ErrorNote from './ErrorNote.vue'
const props = defineProps({
  onSave: { type: Function, required: true },
  restart: { type: Boolean, default: true },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  error: { type: [Object, String], default: null },
  saveLabel: { type: String, default: '保存' },
  // Why the draft cannot be saved yet; shown instead of the save hint.
  problem: { type: String, default: '' },
})
// An awaitable handler keeps saving and lifecycle actions separate.
async function submit(restart = false) {
  await props.onSave()
  await nextTick()
  if (restart && !props.error && !props.dirty) await openRestart()
}
</script>
<template>
  <form class="surface setting-section" @submit.prevent="submit(false)">
    <h2>{{ title }}</h2>
    <p v-if="description" class="muted">{{ description }}</p>
    <fieldset :disabled="saving"><slot /></fieldset>
    <ErrorNote v-if="error" title="没有保存成功" :error="error" class="mt-4" />
    <div class="setting-actions">
      <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || Boolean(problem)">{{ saveLabel }}</v-btn>
      <v-btn v-if="restart" type="button" variant="tonal" :disabled="!dirty || saving || Boolean(problem)" @click="submit(true)">保存并重启</v-btn>
      <span v-if="dirty && problem" class="problem">{{ problem }}</span>
      <span v-else-if="dirty" class="muted">有未保存的修改</span>
    </div>
  </form>
</template>
<style scoped>
.setting-section fieldset{border:0;padding:8px 0 0;margin:0;min-width:0;display:grid;gap:16px}
.setting-actions{display:flex;align-items:center;gap:12px;margin-top:20px;flex-wrap:wrap}
.setting-actions .problem{color:var(--error-text)}
</style>
