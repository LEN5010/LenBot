<script setup>
import { mdiInformationOutline } from '@mdi/js'
defineProps({ text: { type: String, required: true } })
</script>
<template>
  <v-tooltip :text="text" location="top" max-width="360">
    <template #activator="{ props: tip }"><v-icon v-bind="tip" :icon="mdiInformationOutline" size="18" class="field-info" tabindex="0" :aria-label="text" /></template>
  </v-tooltip>
</template>
<style scoped>
.field-info{color:var(--muted);cursor:help}
</style>
