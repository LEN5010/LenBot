<script setup>
// A plain-language title, with the original error text folded underneath.
// Pass `@retry` to offer a retry button.
import Fold from './Fold.vue'
defineProps({ title: { type: String, required: true }, error: { type: [Object, String], required: true }, onRetry: { type: Function, default: null } })
</script>
<template>
  <v-alert type="error" role="alert" :title="title">
    <Fold label="查看错误原文" code>{{ typeof error === 'string' ? error : error.message }}</Fold>
    <template v-if="onRetry" #append><v-btn size="small" variant="text" @click="onRetry()">重试</v-btn></template>
  </v-alert>
</template>
