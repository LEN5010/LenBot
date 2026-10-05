<script setup>
import StatusBadge from './StatusBadge.vue'
defineProps({ status: { type: String, required: true } })
defineEmits(['reconnect'])
</script>
<template>
  <span class="live">
    <StatusBadge dot kind="live" :value="status === 'connected' || status === 'connecting' ? status : 'closed'" />
    <v-btn v-if="status !== 'connected' && status !== 'connecting'" size="small" variant="text" color="primary" @click="$emit('reconnect')">重新连接</v-btn>
  </span>
</template>
<style scoped>
.live{display:inline-flex;align-items:center;gap:var(--sp-1)}
</style>
