<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiForumOutline, mdiAccountOutline, mdiMenu, mdiClose, mdiLogout } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import markUrl from '../assets/lenbot-mark.svg'
const route=useRoute(),router=useRouter(),{mobile}=useDisplay()
const drawer=ref(!mobile.value),busy=ref(false),error=ref('')
const logoutGuard=useRequestGuard()
const sections = [
  { id: 'chat-test', label: '对话测试', icon: mdiForumOutline, to: { name: 'chat-test' } },
  { id: 'chat-test-settings', label: '场景与角色', icon: mdiAccountOutline, to: { name: 'chat-test-settings' } },
]
watch(mobile,value=>drawer.value=!value)
watch(()=>route.fullPath,()=>{
  if(mobile.value)drawer.value=false
})
async function exit(){
  if(busy.value||!window.confirm('退出登录？没保存的修改会丢失。'))return
  const fresh=logoutGuard();
  busy.value=true;
  error.value=''
  try{
    await logout();
    if(useAuth().status==='unauthenticated')await router.replace({name:'login'})
  }
  catch(e){
    if(fresh())error.value=e.message
  }finally{
    if(fresh())busy.value=false
  }
}
</script>
<template>
  <v-navigation-drawer
    v-model="drawer"
    :permanent="!mobile"
    :temporary="mobile"
    :width="224"
    aria-label="主导航"
    class="app-navigation"
  >
    <div class="app-brand">
      <img class="app-mark" :src="markUrl" alt="LenBot" />
      <div><strong>LenBot</strong><span>隔离对话测试</span></div>
      <v-btn
        v-if="mobile"
        :icon="mdiClose"
        variant="text"
        aria-label="关闭导航"
        @click="drawer=false"
      />
    </div>
    <nav class="nav-groups">
      <v-list nav density="compact">
        <v-list-item
          v-for="item in sections"
          :key="item.id"
          :to="item.to"
          :active="route.name===item.id"
          color="primary"
          :prepend-icon="item.icon"
          :title="item.label"
        />
      </v-list>
    </nav>
    <template #append>
      <div class="navigation-footer">
        <v-btn :prepend-icon="mdiLogout" block variant="text" :loading="busy" @click="exit">退出登录</v-btn>
      </div>
    </template>
  </v-navigation-drawer>
  <v-app-bar flat :height="64" class="app-toolbar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer=true" />
    <v-app-bar-title><span class="toolbar-title">{{ route.meta.title }}</span></v-app-bar-title>
    <v-chip size="small" variant="tonal" color="secondary" class="isolated-chip">隔离 · 模拟发送</v-chip>
  </v-app-bar>
  <v-main tag="div">
    <main class="app-page" id="main-content">
      <v-alert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</v-alert>
      <slot />
    </main>
  </v-main>
</template>
<style scoped>
.app-navigation{border-right:1px solid var(--line)}
.app-brand{display:flex;gap:12px;align-items:center;padding:24px 20px 16px}
.app-brand strong{display:block;letter-spacing:-.03em;font-size:21px}
.app-brand span:not(.app-mark){font-size:12px;color:var(--muted)}
.app-mark{width:38px;height:38px;border-radius:10px;display:block;flex:none}
.nav-groups{padding:4px 12px 12px}
.nav-groups :deep(.v-list){padding-top:4px;padding-bottom:0}
.nav-groups :deep(.v-list-item){min-height:42px;border-radius:8px;margin-bottom:3px}
.nav-groups :deep(.v-list-item__prepend > .v-icon){margin-inline-end:14px;font-size:20px;opacity:.85}
.nav-groups :deep(.v-list-item-title){font-size:14px}
.navigation-footer{padding:12px;border-top:1px solid var(--line)}
.app-toolbar{border-bottom:1px solid var(--line)}
.toolbar-title{font-size:14px;font-weight:600}
.isolated-chip{margin-inline:8px 20px;flex:none}
@media(max-width:600px){
  .isolated-chip{margin-inline:4px 12px}
  .toolbar-title{font-size:13px}
  .app-brand{padding:20px 16px}
  .app-brand .v-btn{margin-left:auto}
}
</style>
