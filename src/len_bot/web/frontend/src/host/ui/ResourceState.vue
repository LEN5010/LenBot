<script setup>
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
  <div v-else-if="resource.loading.value" class="loading" :class="{ compact }" role="status" aria-label="读取中">
    <span v-for="index in (compact ? 2 : 3)" :key="index" class="bar" :style="{ '--i': index }" /></div>
</template>
<style scoped>
.loading{display:grid;gap:var(--sp-3);padding:var(--sp-4) 0}
.loading.compact{padding:var(--sp-2) 0;border:0;background:none}
.bar{height:14px;border-radius:7px;background:linear-gradient(90deg,var(--hover) 25%,var(--track) 50%,var(--hover) 75%);background-size:300% 100%;animation:shimmer 1.4s ease-in-out infinite;animation-delay:calc(var(--i) * 120ms)}
.bar:nth-child(1){width:42%}
.bar:nth-child(2){width:88%}
.bar:nth-child(3){width:64%}
@keyframes shimmer{from{background-position:100% 0}to{background-position:0 0}}
</style>
