<script setup>
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
  problem: { type: String, default: '' },
})
async function submit(restart = false) {
  await props.onSave()
  await nextTick()
  if (restart && !props.error && !props.dirty) await openRestart()
}
</script>
<template>
  <Panel tag="form" class="setting-section" :class="{ dirty }" :title="title" :description="description" @submit.prevent="submit(false)">
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
    <fieldset :disabled="saving" class="setting-fields"><slot /></fieldset>
    <ErrorNote v-if="error" title="没有保存成功" :error="error" />
    <template v-if="dirty || error" #footer>
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
@media(min-width:1100px){
  .setting-section{grid-template-columns:minmax(200px,260px) minmax(0,760px);column-gap:var(--sp-6);row-gap:var(--sp-4)}
  .setting-section :deep(> .panel-head){grid-column:1;grid-row:1 / span 3;align-self:start;align-items:flex-start;flex-direction:column;position:sticky;top:88px}
  .setting-section :deep(> .panel-head .panel-title){flex:none;align-self:stretch}
  .setting-section :deep(> .panel-body),.setting-section :deep(> .panel-foot){grid-column:2}
}
.setting-section :deep(> .panel-foot){justify-content:flex-end;animation:rise var(--dur-2) var(--ease-out)}
</style>
