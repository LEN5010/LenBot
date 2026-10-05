<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuth, refreshAuth } from './composables/useAuth.js'
import TrialShell from './layouts/TrialShell.vue'
import HostShell from './layouts/HostShell.vue'
const auth=useAuth(),route=useRoute(),router=useRouter()
async function retry() {
  await refreshAuth();
  if(auth.status==='unauthenticated')router.replace({name:'login',query:{redirect:route.fullPath}})
}
</script>
<template>
  <v-app>
    <div v-if="auth.status==='loading'" class="initial-state" role="status">
      <v-progress-circular indeterminate color="primary" />
      <p>正在确认登录状态…</p>
    </div>
    <div v-else-if="auth.status==='error'" class="initial-state">
      <v-alert type="error" variant="tonal" title="无法读取登录状态">{{ auth.error }}</v-alert>
      <v-btn color="primary" @click="retry">重新读取</v-btn>
    </div>
    <router-view v-else-if="route.name==='login'" />
    <template v-else-if="auth.status==='authenticated'">
      <HostShell v-if="auth.panelContext.mode==='isolated-multi'"><router-view /></HostShell>
      <TrialShell v-else-if="auth.panelContext.mode==='isolated'"><router-view /></TrialShell>
    </template>
  </v-app>
</template>
<style scoped>
.initial-state{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:var(--sp-4);padding:var(--sp-5);color:var(--muted)}
</style>
