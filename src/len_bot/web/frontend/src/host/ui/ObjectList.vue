<script setup>
// Rows of objects. `divided` draws lines between rows (lists inside a panel);
// otherwise rows are separate selectable items (a workbench list).
defineProps({ divided: Boolean })
</script>
<template><ul class="object-list" :class="{ divided }"><slot /></ul></template>
<style scoped>
.object-list{list-style:none;margin:0;padding:0;display:grid;gap:2px;min-width:0}
.object-list.divided{gap:0}
.object-list.divided > :deep(li + li){border-top:1px solid var(--line)}
</style>
