<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '../composables/useAuth.js'
import { returnPath } from '../router/index.js'
const route=useRoute(),router=useRouter()
const username=ref(''),password=ref(''),busy=ref(false),error=ref('')
async function submit(){if(busy.value)return;busy.value=true;error.value='';try{await login(username.value,password.value);password.value='';await router.replace(returnPath(route.query.redirect))}catch(e){error.value=e.status===401?'用户名或密码不正确':e.message}finally{busy.value=false}}
</script>
<template>
  <main class="login-page"><v-card class="login-card"><v-card-text>
    <span class="login-mark">L</span><h1>登录 LenBot</h1><p class="muted">查看运行事实，管理工作与资料。</p>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <form @submit.prevent="submit"><v-text-field v-model="username" label="用户名" autocomplete="username" required :disabled="busy" /><v-text-field v-model="password" label="密码" type="password" autocomplete="current-password" required :disabled="busy" /><v-btn type="submit" color="primary" block :loading="busy" :disabled="!username || !password">登录</v-btn></form>
    <p class="login-note">使用现有管理账户登录</p>
  </v-card-text></v-card></main>
</template>
<style scoped>.login-page{display:grid;place-items:center;min-height:100vh;padding:24px}.login-card{width:420px;max-width:100%;padding:20px}.login-mark{display:grid;place-items:center;width:44px;height:44px;border-radius:10px;background:#2563eb;color:#fff;font-size:26px;font-weight:700;margin-bottom:24px}.login-card h1{font-size:25px;margin-bottom:8px}.login-card form{display:grid;gap:20px;margin-top:28px}.login-note{margin:24px 0 0;font-size:12px;color:var(--muted)}@media(max-width:600px){.login-card{padding:12px}.login-page{padding:16px}}</style>
