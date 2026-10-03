<script setup>
import { computed } from 'vue'
import { sceneName } from '../../api.js'
import { sectionLabel } from '../labels.js'
import { restartFlow as flow, confirmRestart, newPanelUrl, panelChanged, waitForRestart } from '../restart.js'
import ErrorNote from './ErrorNote.vue'
const pending = computed(() => flow.preview ? [...new Set([
  ...flow.preview.pending.sections.map(sectionLabel), ...flow.preview.pending.scenes.map(sceneName),
  ...flow.preview.pending.personas.map(item => `角色 ${item.name}`),
])] : [])
const link = computed(() => flow.preview ? newPanelUrl() : null)
</script>
<template>
  <v-dialog v-model="flow.open" max-width="680" :persistent="flow.busy || flow.waiting">
    <v-card title="重启 LenBot">
      <v-card-text class="restart-content">
        <v-progress-linear v-if="flow.busy" indeterminate />
        <ErrorNote v-if="flow.error" title="重启状态" :error="flow.error" />
        <template v-if="flow.preview && flow.phase === 'preview'">
          <p v-if="pending.length">已保存、将应用：{{ pending.join('、') }}。</p>
          <p v-else>没有待应用修改；本次将按当前根配置重新启动。</p>
          <p>停止接收新工作后，宿主沿现有流程收尾并重启。当前页面未保存的草稿不在本次修改内，重启后需重新登录。</p>
          <p v-if="flow.preview.chats.length">进行中的聊天：{{ flow.preview.chats.map(sceneName).join('、') }}。</p>
          <ul v-if="flow.preview.tasks.length"><li v-for="task in flow.preview.tasks" :key="task.id">任务 #{{ task.id }} · {{ sceneName(task.scene) }} · {{ task.goal }}{{ task.account_browser ? '（账号浏览）' : '' }}</li></ul>
          <p v-if="flow.preview.queued_tasks">排队任务 {{ flow.preview.queued_tasks }} 个，仍保留原任务 ID 和排队状态。</p>
          <p>执行中的普通任务保留会话和文件，中断后可按原 ID 继续；账号任务关闭会话，不原地续接。</p>
          <p v-if="flow.preview.browsers.length">将收尾的账号会话：{{ flow.preview.browsers.map(item => `#${item.id} · ${item.session || '创建中'}`).join('、') }}。</p>
          <p v-if="flow.preview.trials.length">活动试聊 {{ flow.preview.trials.length }} 个将停止，已有记录保留。</p>
          <p v-if="panelChanged()">面板监听地址将改变：<a v-if="link" :href="link">{{ link }}</a><span v-else>新配置关闭面板或使用随机端口，请看启动日志。</span> 使用代理或 SSH 转发时，同步调整其目标地址。</p>
          <p v-if="!flow.preview.restartable">当前直接运行宿主。停止后使用 <code>len-bot</code> 启动器，即可从面板重启。</p>
        </template>
        <p v-if="flow.phase === 'waiting'">正在等待旧宿主退出、新宿主启动。新进程就绪后重新打开登录页；等待期间不重复发起重启。</p>
        <p v-if="flow.phase === 'ready'">新宿主已启动，正在重新打开面板。</p>
        <template v-if="flow.phase === 'address-changed'">
          <p>重启请求已接受；不再轮询旧地址。</p>
          <p v-if="link"><a :href="link">打开新面板地址</a>：{{ link }}</p>
          <p v-else>按新配置查看启动日志中的地址，或从终端管理已关闭面板的实例。</p>
          <p>使用 HTTPS 代理或 SSH 转发时，调整其目标后打开原访问地址。启动错误保留在原终端或服务日志。</p>
        </template>
        <p v-if="flow.phase === 'unavailable' && flow.lastConnectionError" class="muted">最近连接结果：{{ flow.lastConnectionError.message }}</p>
      </v-card-text>
      <v-card-actions>
        <v-btn v-if="!flow.waiting" :disabled="flow.busy" @click="flow.open = false">稍后重启</v-btn><v-spacer />
        <v-btn v-if="flow.phase === 'unavailable'" color="primary" @click="waitForRestart">再次检查连接</v-btn>
        <v-btn v-if="flow.phase === 'preview'" color="primary" :disabled="flow.busy || !flow.preview?.restartable" @click="confirmRestart">确认重启</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
<style scoped>.restart-content{display:grid;gap:14px}.restart-content p{margin:0;overflow-wrap:anywhere}.restart-content ul{padding-left:20px}</style>
