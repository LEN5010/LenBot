<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiForumOutline, mdiAccountOutline, mdiMenu, mdiLogout } from '@mdi/js'
import { logout, useAuth } from '../composables/useAuth.js'
import { confirm } from '../composables/useConfirm.js'
import { host } from '../host/store.js'
import ErrorNote from '../host/ui/ErrorNote.vue'
import ConfirmHost from '../host/ui/ConfirmHost.vue'
import markUrl from '../assets/lenbot-mark.svg'

const route = useRoute(), router = useRouter(), { mobile } = useDisplay()
const drawer = ref(!mobile.value), leaving = ref(false), logoutError = ref(null)
const sections = [
  { id: 'chat-test', label: '对话测试', icon: mdiForumOutline },
  { id: 'chat-test-settings', label: '场景与角色', icon: mdiAccountOutline },
]
const toast = computed({ get: () => Boolean(host.toast), set: value => { if (!value) host.toast = '' } })
watch(mobile, value => { drawer.value = !value })
watch(() => route.fullPath, () => { if (mobile.value) drawer.value = false })

async function exit() {
  if (leaving.value || !await confirm({ title: '退出登录？', text: '未保存的修改会丢失。', confirmLabel: '退出' })) return
  leaving.value = true
  logoutError.value = null
  try {
    await logout()
    if (useAuth().status === 'unauthenticated') await router.replace({ name: 'login' })
  } catch (error) {
    logoutError.value = error
  } finally {
    leaving.value = false
  }
}
</script>

<template>
  <v-navigation-drawer v-model="drawer" :permanent="!mobile" :temporary="mobile" :width="216" aria-label="主导航" class="shell-nav">
    <div class="shell-brand"><img :src="markUrl" alt="" /><div><strong>LenBot</strong><span>隔离对话测试</span></div></div>
    <nav class="shell-nav-groups">
      <div class="shell-nav-group">
        <RouterLink v-for="item in sections" :key="item.id" :to="{ name: item.id }" class="shell-nav-item"
          :class="{ active: route.name === item.id }" :aria-current="route.name === item.id ? 'page' : undefined">
          <v-icon :icon="item.icon" size="18" />{{ item.label }}</RouterLink>
      </div>
    </nav>
    <template #append>
      <div class="shell-nav-footer"><v-btn :prepend-icon="mdiLogout" block variant="text" :loading="leaving" @click="exit">退出登录</v-btn></div>
    </template>
  </v-navigation-drawer>
  <v-app-bar flat :height="56" class="shell-bar">
    <v-btn v-if="mobile" :icon="mdiMenu" variant="text" aria-label="打开导航" @click="drawer = true" />
    <span class="shell-bar-title">{{ route.meta.title }}</span>
    <v-spacer />
    <v-chip class="mr-2">隔离 · 模拟发送</v-chip>
  </v-app-bar>
  <v-main tag="div">
    <main class="shell-main" id="main-content">
      <ErrorNote v-if="logoutError" title="退出登录失败" :error="logoutError" />
      <slot />
    </main>
  </v-main>
  <ConfirmHost />
  <v-snackbar v-model="toast" :timeout="3000" location="bottom">{{ host.toast }}</v-snackbar>
</template>
