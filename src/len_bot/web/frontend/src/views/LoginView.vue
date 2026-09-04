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
      error.value = '当前为默认初始密码，登录后请尽快前往「设置」修改。'
    }
    emit('logged-in')
  } catch (e) {
    error.value = e.message === 'unauthorized' ? '账号或密码错误，请核对后重试' : (e.message || '登录失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="glow-orb"></div>
    <form class="login-card" @submit.prevent="login">
      <div class="brand-header">
        <div class="logo-badge">L</div>
        <h1>LenBot 管理中心</h1>
        <p class="subtitle">查看和管理你的 QQ 机器人</p>
      </div>
      <label>
        <span>管理员账号</span>
        <input v-model="username" autocomplete="username" placeholder="请输入用户名" />
      </label>
      <label>
        <span>访问密码</span>
        <input v-model="password" type="password" autocomplete="current-password" placeholder="请输入密码" />
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary submit-btn" :disabled="busy" type="submit">
        {{ busy ? '正在登录…' : '登录' }}
      </button>
      <div class="login-footer">
        <span>登录后即可查看机器人的运行情况</span>
      </div>
    </form>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: var(--bg-gradient);
  background-attachment: fixed;
}

.glow-orb {
  position: absolute;
  width: 520px;
  height: 520px;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.16) 0%, rgba(99, 102, 241, 0.08) 50%, transparent 70%);
  border-radius: 50%;
  filter: blur(60px);
  pointer-events: none;
}

.login-card {
  position: relative;
  z-index: 1;
  width: 390px;
  background: rgba(255, 255, 255, 0.78);
  backdrop-filter: blur(22px) saturate(135%);
  -webkit-backdrop-filter: blur(22px) saturate(135%);
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: var(--radius-lg);
  padding: 38px 34px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: var(--shadow-bento);
}

.brand-header {
  text-align: center;
  margin-bottom: 6px;
}

.logo-badge {
  width: 52px;
  height: 52px;
  margin: 0 auto 10px;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 1.5rem;
  font-weight: 800;
  border-radius: 15px;
  background: var(--accent-gradient);
  box-shadow: 0 10px 24px var(--accent-glow);
}

.brand-header h1 {
  font-size: 1.35rem;
}

.subtitle {
  color: var(--muted);
  font-size: 0.86rem;
  margin: 6px 0 0;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.86rem;
  color: var(--text-soft);
  font-weight: 650;
}

label input {
  padding: 10px 14px;
}

.submit-btn {
  margin-top: 8px;
  padding: 11px;
  font-size: 0.95rem;
  border-radius: var(--radius-sm);
}

.error {
  color: var(--bad);
  background: var(--bad-bg);
  border: 1px solid rgba(220, 38, 38, 0.16);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  font-size: 0.84rem;
  margin: 0;
}

.login-footer {
  text-align: center;
  font-size: 0.78rem;
  color: var(--muted);
  margin-top: 4px;
}
</style>
