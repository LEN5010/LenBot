<script setup>
import { mdiHelpCircleOutline } from '@mdi/js'

// The place for the paragraph that used to sit on the panel like a code
// comment. The field keeps a one-line hint; the reasoning lives in here.
defineProps({
  text: { type: String, required: true },
  label: { type: String, default: '查看说明' },
})
</script>

<template>
  <v-menu :close-on-content-click="false" location="bottom start" max-width="440">
    <template #activator="{ props }">
      <v-btn
        v-bind="props"
        variant="text"
        size="x-small"
        density="comfortable"
        icon
        :aria-label="label"
        class="help-hint__btn"
      >
        <v-icon :icon="mdiHelpCircleOutline" size="17" />
      </v-btn>
    </template>
    <v-card class="help-hint__card">
      <v-card-text class="help-hint__text">{{ text }}</v-card-text>
    </v-card>
  </v-menu>
</template>

<style scoped>
.help-hint__btn{ color:var(--muted);vertical-align:baseline }
.help-hint__btn:hover{ color:var(--primary) }
.help-hint__text{ white-space:pre-line;line-height:1.75;font-size:13px;color:var(--ink) }
</style>
