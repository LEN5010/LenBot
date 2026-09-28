<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, sceneName, fmtTime } from '../api.js'
import ChatTestView from './ChatTestView.vue'
import { useRequestGuard } from '../composables/useRequestGuard.js'
const route = useRoute(), router = useRouter()
const host = ref(null), trials = ref([]), loading = ref(false), busy = ref(false), error = ref('')
const scene = ref(''), acknowledged = ref(false)
const beginRead = useRequestGuard()
const options = computed(() => host.value?.scenes.map(item => ({ value: item.scene, title: `${sceneName(item.scene)} · ${item.persona.name}` })) || [])
const selected = computed(() => trials.value.find(item => item.id === route.query.trial) || null)
const active = computed(() => trials.value.find(item => item.active))
async function read() {
  const fresh = beginRead(); loading.value = true
  try {
    const [state, result] = await Promise.all([api('/api/host/state'), api('/api/host/trials')])
    if (!fresh()) return
    host.value = state; trials.value = result.items; error.value = ''
    if (!scene.value) scene.value = state.scenes.some(item => item.scene === route.query.scene) ? route.query.scene : state.scenes[0]?.scene || ''
    if (!route.query.trial && active.value) await select(active.value)
  } catch (err) { if (fresh()) error.value = err.message }
  finally { if (fresh()) loading.value = false }
}
function select(trial) { return router.push({ name: 'host-trials', query: { scene: trial.scene, trial: trial.id } }) }
async function start() {
  if (busy.value || !scene.value || !acknowledged.value) return
  busy.value = true; error.value = ''; beginRead()
  try {
    const trial = await api('/api/host/trials', { method: 'POST', body: JSON.stringify({ scene: scene.value, acknowledge_model_cost: true }) })
    await select(trial); acknowledged.value = false; await read()
  } catch (err) { error.value = `${err.message} 不会自动重试；若结果未确认，请重读列表。` }
  finally { busy.value = false }
}
async function stop() {
  if (!active.value || busy.value || !window.confirm('停止当前试聊？会取消在途模型请求，保留已有测试记录，不影响 QQ 和生产会话。')) return
  busy.value = true; error.value = ''
  try { await api(`/api/host/trials/${encodeURIComponent(active.value.id)}/stop`, { method: 'POST' }); await read() }
  catch (err) { error.value = `${err.message} 停止结果未确认，请重读。` }
  finally { busy.value = false }
}
onMounted(read)
</script>
<template>
  <div class="page-stack host-trials">
    <section class="surface">
      <div class="section-heading"><div><h1>对话测试</h1><p class="muted">复制正在运行的场景与角色，单独试聊；绝不把真实出口改成模拟模式。</p></div>
        <v-btn variant="outlined" :loading="loading" :disabled="busy" @click="read">重读试聊列表</v-btn></div>
      <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
      <p>模型会真实调用并可能计费，测试用量与生产记录分开，使用相同模型并发槽。停止不退回已发生的费用。</p>
      <p class="muted">使用独立会话、消息库、媒体和模拟提醒。本地记忆从空白目录开始；本次不开放工作任务、文件上传、插件、MCP、账号浏览、平台查询/语音转写及公网读取。还不支持导入真实上下文。</p>
      <form v-if="!active" class="start-form" @submit.prevent="start">
        <v-select v-model="scene" :items="options" label="使用哪个场景的运行快照" :disabled="busy || loading" hide-details="auto" />
        <v-checkbox v-model="acknowledged" label="我知道模型会真实调用并计费，发送始终模拟" :disabled="busy" hide-details />
        <v-btn type="submit" color="primary" :disabled="!acknowledged || !scene || busy || loading" :loading="busy">新建独立试聊</v-btn>
      </form>
      <div v-else class="active-trial"><p>当前活动试聊：{{ sceneName(active.scene) }} · {{ active.persona }}。离开页面不会自动停止。</p>
        <v-btn variant="tonal" @click="select(active)">查看活动试聊</v-btn><v-btn color="warning" variant="outlined" :loading="busy" :disabled="busy" @click="stop">停止试聊</v-btn></div>
      <p v-if="route.query.trial && !selected && !loading" role="status" class="muted">本次宿主启动中没有该试聊。重启不自动恢复旧试聊，磁盘记录未删除。</p>
      <ul v-if="trials.length" class="trial-list"><li v-for="trial in trials" :key="trial.id">
        <v-btn variant="text" :active="selected?.id === trial.id" @click="select(trial)">{{ sceneName(trial.scene) }} · {{ fmtTime(trial.created) }} · {{ trial.active ? '活动中' : '已停止' }}</v-btn>
      </li></ul>
    </section>
    <section v-if="selected" class="surface">
      <h2>本次实际范围</h2><p>{{ selected.memory }}；大脑 {{ selected.models.mind }}，表达 {{ selected.models.voice }}。</p>
      <p class="muted">未接入的生产工具：{{ selected.excluded_tools.join('、') || '无额外工具' }}。虚拟身份不能获得主人账号能力。</p>
      <details><summary>测试文件位置</summary><code>{{ selected.root }}</code></details>
    </section>
    <ChatTestView v-if="selected" :key="selected.id" :api-base="`/api/host/trials/${encodeURIComponent(selected.id)}`" />
  </div>
</template>
<style scoped>
.host-trials{max-width:1200px;margin-inline:auto}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
h1{margin-top:0}h2{font-size:18px}.surface{overflow-wrap:anywhere;min-width:0}.start-form{display:grid;gap:12px;max-width:680px}.active-trial{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.active-trial p{flex-basis:100%}
.trial-list{list-style:none;padding:0}.host-trials :deep(.v-btn),summary{min-height:44px}.host-trials code{white-space:pre-wrap;overflow-wrap:anywhere}
</style>
