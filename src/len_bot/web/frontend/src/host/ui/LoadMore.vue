<script setup>
defineProps({ loading: Boolean, label: { type: String, default: '显示更多' } })
defineEmits(['more'])
</script>
<template><div class="load-more"><v-btn variant="text" size="small" :loading="loading" @click="$emit('more')">{{ label }}</v-btn></div></template>
<style scoped>
.load-more{display:flex;justify-content:center;padding-top:var(--sp-2)}
</style>
