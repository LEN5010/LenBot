<script setup>
defineProps({ label: { type: String, default: '高级设置' } })
</script>
<template>
  <v-expansion-panels class="advanced-fields">
    <v-expansion-panel :title="label" elevation="0">
      <v-expansion-panel-text><div class="advanced-grid"><slot /></div></v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>
<style scoped>
.advanced-fields{border:1px solid var(--line);border-radius:var(--radius)}
.advanced-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:var(--sp-4);padding-top:var(--sp-2);align-items:start}
</style>
