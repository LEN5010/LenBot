<script setup>
defineProps({ label: { type: String, required: true }, code: Boolean, open: Boolean })
</script>
<template>
  <details class="fold" :open="open">
    <summary>{{ label }}</summary>
    <pre v-if="code" class="fold-code"><slot /></pre>
    <div v-else class="fold-body"><slot /></div>
  </details>
</template>
<style scoped>
.fold summary{cursor:pointer;color:var(--muted);font-size:var(--fs-sm);width:max-content;max-width:100%}
.fold summary:hover{color:var(--ink)}
.fold-body{display:grid;gap:var(--sp-2);margin-top:var(--sp-2)}
.fold-code{margin:var(--sp-2) 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--mono);font-size:var(--fs-xs);background:var(--code-bg);padding:var(--sp-3);border-radius:var(--radius);color:var(--ink)}
</style>
