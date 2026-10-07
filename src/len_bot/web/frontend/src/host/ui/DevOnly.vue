<script setup>
import { developerDetails } from '../../composables/useDeveloperMode.js'
import Fold from './Fold.vue'
defineProps({ label: { type: String, default: '' }, json: { default: undefined } })
</script>
<template>
  <template v-if="developerDetails">
    <Fold v-if="label" :label="label" class="dev-only" :code="json !== undefined">
      <template v-if="json !== undefined">{{ JSON.stringify(json, null, 2) }}</template><slot v-else /></Fold>
    <slot v-else />
  </template>
</template>
<style scoped>
.dev-only{border-top:1px dashed var(--line);padding-top:var(--sp-2)}
</style>
