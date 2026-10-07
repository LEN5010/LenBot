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
.advanced-fields{border-top:1px solid var(--line);border-radius:0}
.advanced-fields :deep(.v-expansion-panel){background:transparent}
.advanced-fields :deep(.v-expansion-panel-title){padding:var(--sp-3) 0;min-height:44px;font-weight:600;color:var(--muted)}
.advanced-fields :deep(.v-expansion-panel-title__overlay){display:none}
.advanced-fields :deep(.v-expansion-panel-title:focus-visible){outline:2px solid var(--primary);outline-offset:2px;border-radius:var(--radius-sm)}
.advanced-fields :deep(.v-expansion-panel-title:hover){color:var(--ink)}
.advanced-fields :deep(.v-expansion-panel-text__wrapper){padding:0 0 var(--sp-2)}
.advanced-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:var(--sp-4);padding-top:var(--sp-2);align-items:start}
</style>
