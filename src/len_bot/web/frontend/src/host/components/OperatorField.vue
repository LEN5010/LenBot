<script setup>
import { host } from '../store.js'
</script>
<template>
  <v-text-field v-model="host.operator" label="你的 QQ" inputmode="numeric" class="operator"
    hint="新建、回答、取消时按这个 QQ 判断权限" persistent-hint />
</template>
<style scoped>
.operator{flex:0 1 240px;min-width:180px}
</style>
