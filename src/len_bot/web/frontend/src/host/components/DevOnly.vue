<script setup>
import { developerDetails } from '../../composables/useDeveloperMode.js'
defineProps({ label: { type: String, default: '' } })
</script>
<template>
  <template v-if="developerDetails">
    <details v-if="label" class="dev-only"><summary>{{ label }}</summary><slot /></details>
    <slot v-else />
  </template>
</template>
<style scoped>
.dev-only{border-top:1px dashed var(--line);padding-top:8px;margin-top:8px}
.dev-only summary{cursor:pointer;color:var(--muted);font-size:13px}
.dev-only :deep(pre){white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;background:var(--code-bg);padding:12px;border-radius:8px}
</style>
