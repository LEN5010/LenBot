<script setup>
import { ref } from 'vue'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import ChatTestView from '../views/ChatTestView.vue'

const props = defineProps({ scene: { type: String, required: true }, directory: { type: String, required: true }, files: { type: Object, default: null }, disabled: Boolean })
const acknowledged = ref(false), contextMessages = ref(0)
const trial = ref(null), busy = ref(false), error = ref('')
const beginOperation = useRequestGuard()

async function start() {
  if (busy.value || props.disabled || !props.files || !props.scene || !acknowledged.value) return
  const body = { scene: props.scene, acknowledge_model_cost: true,
    context_messages: Number(contextMessages.value), persona_draft: { directory: props.directory, files: { ...props.files } } }
  const fresh = beginOperation()
  busy.value = true
  error.value = ''
  try {
    const result = await api('/api/host/trials', { method: 'POST', body: JSON.stringify(body) })
    if (!fresh()) return
    trial.value = result
    acknowledged.value = false
  } catch (problem) {
    if (fresh()) error.value = problem.status >= 400 && problem.status < 500
      ? `未开始新试聊：${problem.message} 当前编辑原文未保存，也未丢弃。`
      : `试聊创建结果未确认：${problem.message} 请到统一试聊列表手动核对；不会自动重复开始。`
  } finally {
    if (fresh()) busy.value = false
  }
}

async function stop() {
  if (busy.value || !trial.value?.active || !window.confirm('停止这个草稿试聊？在途模型请求会取消，角色编辑原文和生产会话不变。')) return
  const id = trial.value.id, fresh = beginOperation()
  busy.value = true
  error.value = ''
  try {
    const result = await api(`/api/host/trials/${encodeURIComponent(id)}/stop`, { method: 'POST' })
    if (fresh()) trial.value = result
  } catch (problem) {
    if (fresh()) error.value = `停止结果未确认：${problem.message} 请到统一试聊列表核对；不会自动重复停止。`
  } finally {
    if (fresh()) busy.value = false
  }
}
</script>

<template>
  <section class="surface persona-draft-trial" aria-labelledby="persona-draft-trial-title">
    <div class="section-heading">
      <div><h2 id="persona-draft-trial-title">用当前四文件草稿独立试聊</h2>
        <p class="muted">不先保存生产角色包。四文件取当前编辑原文与本页选集，知识、表情与头像取已确认包；绑定改变时拒绝混搭。来源：{{ directory }}。</p></div>
      <v-btn variant="outlined" :to="{name:'host-trials',query:{scene}}">统一试聊列表</v-btn>
    </div>
    <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
    <v-alert type="info" variant="tonal">始终模拟发送，使用当前运行场景和模型绑定。试聊范围不包含生产任务、插件、MCP、账号浏览或平台查询。模型请求会真实计费，但创建本身不会自动发言。</v-alert>
    <form v-if="!trial?.active" class="draft-trial-form" @submit.prevent="start">
      <v-select v-model="contextMessages" :items="[{title:'空白对话',value:0},{title:'复制最近20条原文',value:20},{title:'复制最近50条原文',value:50},{title:'复制最近100条原文',value:100}]" label="聊天背景" hide-details :disabled="busy || disabled" />
      <v-checkbox v-model="acknowledged" label="我知道后续试聊会真实调用模型并计费，发送始终模拟" hide-details :disabled="busy || disabled" />
      <v-btn type="submit" color="primary" :loading="busy" :disabled="busy || disabled || !acknowledged || !files || !scene">用此草稿新建独立试聊</v-btn>
    </form>
    <template v-if="trial">
      <p>本次已冻结草稿：{{ sceneName(trial.scene) }} · {{ trial.persona }} · {{ trial.active ? '活动中' : '已停止' }}。继续编辑上方不会改变此试聊角色。</p>
      <p class="muted">排除的工具：{{ trial.excluded_tools.join('、') || '无额外排除' }}；{{ trial.memory }}。离页不会自动停止，可在统一列表查看或停止。</p>
      <details><summary>本次原草稿与实际运行文件</summary><p>原四文件：<code>{{ trial.draft_path }}</code></p><p>试聊根：<code>{{ trial.root }}</code></p></details>
      <details v-if="trial.context.length"><summary>已复制的聊天背景（{{ trial.context.length }} 条）</summary><pre>{{ trial.context.join('\n\n') }}</pre></details>
      <v-btn v-if="trial.active" variant="outlined" color="warning" :loading="busy" :disabled="busy" @click="stop">停止这个草稿试聊</v-btn>
      <ChatTestView :key="trial.id" :api-base="`/api/host/trials/${encodeURIComponent(trial.id)}`" />
    </template>
  </section>
</template>

<style scoped>
.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
h2{font-size:18px;margin:0}.section-heading p{margin:8px 0 16px}.draft-trial-form{display:grid;gap:12px;max-width:680px;margin:16px 0}
pre,code,p{white-space:pre-wrap;overflow-wrap:anywhere}pre{max-height:320px;overflow:auto}summary{min-height:44px;padding-top:12px}
.persona-draft-trial :deep(.v-btn){min-height:44px}.persona-draft-trial :deep(.chat-test){margin-top:16px}
</style>
