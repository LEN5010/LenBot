<script setup>
const sizes = { sm: 520, md: 720, lg: 960 }
defineProps({
  modelValue: Boolean,
  title: { type: String, required: true },
  size: { type: String, default: 'sm' },
  busy: Boolean,
  cancelLabel: { type: String, default: '取消' },
  persistent: Boolean,
})
const emit = defineEmits(['update:modelValue'])
</script>
<template>
  <v-dialog :model-value="modelValue" :max-width="sizes[size]" scrollable :persistent="persistent || busy"
    @update:model-value="value => emit('update:modelValue', value)">
    <v-card :title="title" class="form-dialog">
      <v-card-text class="form-dialog-body"><slot /></v-card-text>
      <v-card-actions class="form-dialog-actions">
        <slot name="danger" /><v-spacer />
        <v-btn variant="text" :disabled="busy" @click="emit('update:modelValue', false)">{{ cancelLabel }}</v-btn>
        <slot name="actions" />
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
<style scoped>
.form-dialog{border-color:var(--line);box-shadow:var(--shadow-float)}
.form-dialog :deep(.v-card-item){padding:var(--sp-5) var(--sp-5) 0}
.form-dialog :deep(.v-card-title){font-size:var(--fs-xl);font-weight:700;padding:0}
.form-dialog-body{display:grid;gap:var(--sp-4);padding:var(--sp-4) var(--sp-5);align-content:start}
.form-dialog-body :deep(p){margin:0}
.form-dialog-actions{padding:var(--sp-3) var(--sp-4) var(--sp-4);gap:var(--sp-2);flex-wrap:wrap}
</style>
