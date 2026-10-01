<script setup>
// Sticky save bar for a page-sized form that is saved in one go.
import ErrorNote from './ErrorNote.vue'
defineProps({
  dirty: { type: Boolean, required: true },
  saving: { type: Boolean, default: false },
  error: { type: [Object, String], default: null },
  problem: { type: String, default: '' },
  label: { type: String, default: '保存' },
})
defineEmits(['discard'])
</script>
<template>
  <div class="save-bar" :class="{ show: dirty || error }">
    <ErrorNote v-if="error" title="没有保存成功" :error="error" />
    <div class="save-row">
      <span :class="{ problem }">{{ problem || (dirty ? '有未保存的修改' : '') }}</span>
      <v-btn variant="text" :disabled="!dirty || saving" @click="$emit('discard')">放弃修改</v-btn>
      <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty || Boolean(problem)">{{ label }}</v-btn>
    </div>
  </div>
</template>
<style scoped>
.save-bar{position:sticky;bottom:0;z-index:2;display:none;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 16px;box-shadow:0 -4px 16px rgba(23,43,70,.08)}
.save-bar.show{display:grid}
.save-row{display:flex;align-items:center;justify-content:flex-end;gap:8px}
.save-row span{margin-right:auto;color:var(--muted)}
.save-row span.problem{color:var(--error-text)}
</style>
