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
      error.value = '当前为默认初始密码，登录后请尽快前往「系统设置」修改。'
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
        <div class="logo-badge">🤖</div>
        <h1>LenBot 控制中心</h1>
        <p class="subtitle">社交持久化智能体 · 集中运维中枢</p>
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
        {{ busy ? '身份验证中…' : '登录控制台' }}
      </button>
      <div class="login-footer">
        <span>安全会话加密 · 实时状态同步</span>
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
  background: radial-gradient(circle at 50% 50%, rgba(99, 102, 241, 0.12) 0%, rgba(56, 189, 248, 0.05) 35%, transparent 70%);
}

.glow-orb {
  position: absolute;
  width: 500px;
  height: 500px;
  background: radial-gradient(circle, rgba(59, 130, 246, 0.18) 0%, rgba(99, 102, 241, 0.08) 50%, transparent 70%);
  border-radius: 50%;
  filter: blur(60px);
  pointer-events: none;
}

.login-card {
  position: relative;
  z-index: 1;
  width: 380px;
  background: rgba(18, 24, 38, 0.78);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  padding: 36px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: 0 20px 50px -10px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.05) inset;
}

.brand-header {
  text-align: center;
  margin-bottom: 8px;
}

.logo-badge {
  font-size: 2.2rem;
  margin-bottom: 6px;
}

h1 {
  font-size: 1.35rem;
  font-weight: 700;
  margin: 0;
  justify-content: center;
  background: linear-gradient(135deg, #ffffff 40%, #94a3b8 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.subtitle {
  color: var(--muted);
  font-size: 0.84rem;
  margin: 6px 0 0;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 0.86rem;
  color: #cbd5e1;
  font-weight: 500;
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
  color: #f87171;
  background: var(--bad-bg);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  font-size: 0.84rem;
  margin: 0;
}

.login-footer {
  text-align: center;
  font-size: 0.76rem;
  color: var(--muted);
  margin-top: 4px;
}
</style>
