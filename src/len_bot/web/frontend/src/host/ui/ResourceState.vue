<script setup>
// The states of one useResource read: loading the first time, failed (with
// retry), empty, or the content. A failed refresh keeps the old content.
import ErrorNote from './ErrorNote.vue'
import EmptyState from './EmptyState.vue'
defineProps({
  resource: { type: Object, required: true },
  errorTitle: { type: String, default: '读取失败' },
  empty: Boolean,
  emptyText: { type: String, default: '还没有内容' },
  compact: Boolean,
})
</script>
<template>
  <ErrorNote v-if="resource.error.value" :title="errorTitle" :error="resource.error.value" @retry="resource.reload()" />
  <template v-if="resource.data.value !== null && resource.data.value !== undefined">
    <EmptyState v-if="empty" :text="emptyText" :compact="compact"><slot name="empty" /></EmptyState>
    <slot v-else :data="resource.data.value" />
  </template>
  <div v-else-if="resource.loading.value" class="loading" role="status">
    <v-progress-circular indeterminate size="18" width="2" color="primary" />读取中…</div>
</template>
<style scoped>
.loading{display:flex;align-items:center;justify-content:center;gap:var(--sp-2);padding:var(--sp-5);color:var(--muted);font-size:var(--fs-sm)}
</style>
