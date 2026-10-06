<script setup>
// One independently saved block of settings.
import { nextTick } from 'vue'
import { openRestart } from '../restart.js'
import Panel from './Panel.vue'
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
  <Panel tag="form" :title="title" :description="description" @submit.prevent="submit(false)">
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
    <fieldset :disabled="saving" class="setting-fields"><slot /></fieldset>
    <ErrorNote v-if="error" title="没有保存成功" :error="error" />
    <template #footer>
      <span v-if="dirty && problem" class="problem hint">{{ problem }}</span>
      <span v-else class="muted hint">{{ dirty ? '有未保存的修改' : '' }}</span>
      <v-btn v-if="restart" type="button" variant="outlined" :disabled="!dirty || saving || Boolean(problem)" @click="submit(true)">保存并重启</v-btn>
      <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || Boolean(problem)">{{ saveLabel }}</v-btn>
    </template>
  </Panel>
</template>
<style scoped>
.setting-fields{border:0;padding:0;margin:0;min-width:0;display:grid;gap:var(--sp-4)}
.hint{margin-right:auto;font-size:var(--fs-sm)}
</style>
