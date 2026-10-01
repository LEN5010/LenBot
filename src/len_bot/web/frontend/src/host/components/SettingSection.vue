<script setup>
// One independently saved block of settings.
import ErrorNote from './ErrorNote.vue'
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  error: { type: [Object, String], default: null },
  saveLabel: { type: String, default: '保存' },
})
defineEmits(['save'])
</script>
<template>
  <form class="surface setting-section" @submit.prevent="$emit('save')">
    <h2>{{ title }}</h2>
    <p v-if="description" class="muted">{{ description }}</p>
    <fieldset :disabled="saving"><slot /></fieldset>
    <ErrorNote v-if="error" title="没有保存成功" :error="error" class="mt-4" />
    <div class="setting-actions">
      <v-btn type="submit" color="primary" :loading="saving" :disabled="!dirty">{{ saveLabel }}</v-btn>
      <span v-if="dirty" class="muted">有未保存的修改</span>
    </div>
  </form>
</template>
<style scoped>
.setting-section fieldset{border:0;padding:8px 0 0;margin:0;min-width:0;display:grid;gap:16px}
.setting-actions{display:flex;align-items:center;gap:12px;margin-top:20px;flex-wrap:wrap}
</style>
