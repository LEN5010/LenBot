<script setup>
import { computed } from 'vue'
import { statusInfo } from '../domain/status.js'
const props = defineProps({ domain: { type:String, required:true }, status: [String, Boolean] })
const info = computed(() => statusInfo(props.domain, props.status))
</script>
<template><v-chip class="status-badge" :color="info.color" size="small" variant="tonal" label>{{ info.label }}</v-chip></template>
<style scoped>.status-badge { flex:none;white-space:nowrap;vertical-align:middle;max-width:100% }</style>
