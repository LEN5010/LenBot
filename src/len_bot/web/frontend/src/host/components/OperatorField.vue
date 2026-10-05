<script setup>
import { mdiAccountKeyOutline } from '@mdi/js'
import { host } from '../store.js'
</script>
<template>
  <v-text-field v-model="host.operator" label="你的 平台账号"  class="operator" :prepend-inner-icon="mdiAccountKeyOutline"
    hint="新建、回答、取消时按这个 平台账号 判断权限" hide-details="auto" />
</template>
<style scoped>
.operator{flex:0 1 220px;min-width:180px}
</style>
