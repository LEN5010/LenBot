<script setup>
// Preformatted text: raw records, file contents, logs.
defineProps({ text: { type: String, default: '' } })
</script>
<template><pre class="code-block"><slot>{{ text }}</slot></pre></template>
<style scoped>
.code-block{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--mono);font-size:var(--fs-xs);line-height:1.6;background:var(--code-bg);padding:var(--sp-3);border-radius:var(--radius);max-height:480px;overflow:auto}
</style>
