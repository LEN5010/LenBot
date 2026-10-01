<script setup>
defineProps({ status: { type: String, required: true } })
defineEmits(['reconnect'])
</script>
<template>
  <span v-if="status === 'connected'" class="live ok"><span class="dot" />实时更新中</span>
  <span v-else-if="status === 'connecting'" class="live"><span class="dot" />正在连接…</span>
  <span v-else class="live off"><span class="dot" />实时更新已断开
    <v-btn size="small" variant="text" color="primary" @click="$emit('reconnect')">重新连接</v-btn></span>
</template>
<style scoped>
.live{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--status-idle)}
.ok .dot{background:var(--success)}
.off{color:var(--warning-text)}
.off .dot{background:var(--status-warning)}
</style>
