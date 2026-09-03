<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const emit = defineEmits(['logged-in'])
const username = ref('admin')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function login() {
  error.value = ''
  busy.value = true
  try {
    const res = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: username.value, password: password.value }),
    })
    if (res.is_default_password) {
      error.value = '当前为默认密码，登录后请前往 Settings & Security 修改。'
    }
    emit('logged-in', res.token)
  } catch (e) {
    error.value = e.message === 'unauthorized' ? '认证已过期，请重新登录' : (e.message || '登录失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="login-card" @submit.prevent="login">
      <h1>LenBot Control Plane</h1>
      <p class="muted">Persistent Social Agent · 运维控制面</p>
      <label>用户名 <input v-model="username" autocomplete="username" /></label>
      <label>密码 <input v-model="password" type="password" autocomplete="current-password" /></label>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary" :disabled="busy" type="submit">{{ busy ? '登录中…' : '登录' }}</button>
    </form>
  </div>
</template>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card {
  width: 340px; background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; display: flex; flex-direction: column; gap: 12px;
}
label { display: flex; flex-direction: column; gap: 5px; font-size: 0.88rem; color: var(--muted); }
.error { color: var(--bad); font-size: 0.85rem; margin: 0; }
</style>
