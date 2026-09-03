<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const me = ref(null)
const social = ref({})
const shadow = ref({ enabled: false })
const form = ref({ current_password: '', new_password: '' })
const message = ref('')
const error = ref('')

onMounted(load)
async function load() {
  try {
    me.value = await api('/api/auth/me')
    const overview = await api('/api/overview/stats')
    social.value = overview.social_metrics || {}
    shadow.value.enabled = !!overview.stats?.shadow_mode
  } catch (e) { error.value = e.message }
}

async function changePassword() {
  error.value = ''; message.value = ''
  try {
    await api('/api/auth/change_password', {
      method: 'POST',
      body: JSON.stringify({ current_password: form.value.current_password, new_password: form.value.new_password }),
    })
    message.value = '密码已修改'
    form.value = { current_password: '', new_password: '' }
    await load()
  } catch (e) { error.value = e.message }
}

async function toggleShadow() {
  error.value = ''; message.value = ''
  try {
    const res = await api('/api/cockpit/shadow/toggle', {
      method: 'POST', body: JSON.stringify({ enabled: !shadow.value.enabled }),
    })
    shadow.value.enabled = res.shadow_mode
    message.value = res.shadow_mode ? 'Shadow Mode 已开启：认知照常运行，但不会实发任何消息' : 'Shadow Mode 已关闭：恢复正常发送'
  } catch (e) { error.value = e.message }
}
</script>

<template>
  <div>
    <h1>Settings & Security</h1>
    <p v-if="message" class="tag ok">{{ message }}</p>
    <p v-if="error" class="tag bad">{{ error }}</p>

    <div class="panel" v-if="me">
      <h3 style="margin-top:0">账号</h3>
      <div class="kv"><span class="k">用户</span><span>{{ me.username }}</span></div>
      <div class="kv"><span class="k">默认密码</span>
        <span :class="me.is_default_password ? 'tag bad' : 'tag ok'">{{ me.is_default_password ? '是，请立即修改' : '否' }}</span></div>
      <div class="kv"><span class="k">上次登录</span><span>{{ me.last_login_at ? new Date(me.last_login_at * 1000).toLocaleString() : '—' }}</span></div>

      <h4 class="muted">修改密码</h4>
      <div class="toolbar">
        <input v-model="form.current_password" type="password" placeholder="当前密码" />
        <input v-model="form.new_password" type="password" placeholder="新密码(至少6位)" />
        <button class="primary" @click="changePassword">修改</button>
      </div>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">Shadow Mode(上线阶段控制)</h3>
      <p class="muted">开启后 Runtime 正常 ingest / attention / cognition / 提案，但绝不实发消息，只记录"本来会发送什么"。用于观察 false positive speaking、stale response 与尴尬参与。</p>
      <button :class="shadow.enabled ? 'danger' : 'primary'" @click="toggleShadow">
        {{ shadow.enabled ? '关闭 Shadow Mode' : '开启 Shadow Mode' }}
      </button>
      <div class="kv" style="margin-top:10px"><span class="k">Would-send 记录数</span><span>{{ social.would_send || 0 }}</span></div>
    </div>
  </div>
</template>
