<script setup>
import { confirmState, settleConfirm } from '../../composables/useConfirm.js'
import FormDialog from './FormDialog.vue'
</script>
<template>
  <FormDialog :model-value="confirmState.open" :title="confirmState.title" @update:model-value="value => { if (!value) settleConfirm(false) }">
    <p v-if="confirmState.text" class="confirm-text">{{ confirmState.text }}</p>
    <template #actions>
      <v-btn :color="confirmState.danger ? 'error' : 'primary'" @click="settleConfirm(true)">{{ confirmState.confirmLabel }}</v-btn>
    </template>
  </FormDialog>
</template>
<style scoped>
.confirm-text{white-space:pre-line}
</style>
