<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login, useAuth } from '../composables/useAuth.js'
import { returnPath } from '../router/index.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import markUrl from '../assets/lenbot-mark-tile.svg'
const route=useRoute(),router=useRouter()
const isolated=computed(()=>useAuth().panelContext?.mode==='isolated')
const username=ref(''),password=ref(''),busy=ref(false),error=ref('')
const guard=useRequestGuard()
async function submit(){
  if(busy.value)return
  const fresh=guard(), destination=returnPath(route.query.redirect)
  busy.value=true;
  error.value=''
  try{
    const accepted=await login(username.value,password.value)
    if(!fresh())return
    password.value=''
    if(accepted)await router.replace(destination)
  }catch(e){
    if(fresh())error.value=e.status===401?'用户名或密码不正确':e.message
  }
  finally{
    if(fresh())busy.value=false
  }
}
</script>
<template>
  <main class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <img class="login-mark" :src="markUrl" alt="" />
      <div>
        <h1>欢迎回来</h1>
        <p class="muted">{{ isolated ? '登录后和 Bot 试聊，回复不会发到 QQ。' : '登录 LenBot 控制台，管理你的 Bot。' }}</p>
      </div>
      <v-alert v-if="error" type="error" role="alert">{{ error }}</v-alert>
      <v-text-field v-model="username" label="用户名" autocomplete="username" required :disabled="busy" density="comfortable" />
      <v-text-field v-model="password" label="密码" type="password" autocomplete="current-password" required :disabled="busy" density="comfortable" />
      <v-btn type="submit" color="primary" block size="large" :loading="busy" :disabled="!username || !password">登录</v-btn>
    </form>
  </main>
</template>
<style scoped>
.login-page{display:grid;place-items:center;min-height:100vh;padding:var(--sp-5);background:var(--page)}
.login-card{width:400px;max-width:100%;display:grid;gap:var(--sp-4);padding:var(--sp-6);background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:var(--shadow-card)}
.login-mark{width:40px;height:40px;border-radius:var(--radius)}
.login-card h1{font-size:var(--fs-xl)}
.login-card p{margin:var(--sp-1) 0 0}
@media(max-width:600px){.login-page{padding:var(--sp-4)}.login-card{padding:var(--sp-5)}}
</style>
