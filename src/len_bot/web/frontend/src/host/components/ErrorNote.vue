<script setup>
// A plain-language title, with the original error text folded underneath.
defineProps({ title: { type: String, required: true }, error: { type: [Object, String], required: true } })
</script>
<template>
  <v-alert type="error" variant="tonal" role="alert" :title="title">
    <details class="error-original"><summary>查看错误原文</summary>
      <pre>{{ typeof error === 'string' ? error : error.message }}</pre></details>
  </v-alert>
</template>
<style scoped>
.error-original summary{cursor:pointer;margin-top:4px}
.error-original pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 0;font-size:13px}
</style>
