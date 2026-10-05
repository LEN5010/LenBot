<script setup>
import { computed } from 'vue'
import { sceneName } from '../../api.js'
import { sectionLabel } from '../labels.js'
import { restartFlow as flow, confirmRestart, newPanelUrl, panelChanged, waitForRestart } from '../restart.js'
import ErrorNote from '../ui/ErrorNote.vue'
import FormDialog from '../ui/FormDialog.vue'
const pending = computed(() => flow.preview ? [...new Set([
  ...flow.preview.pending.sections.map(sectionLabel), ...flow.preview.pending.scenes.map(sceneName),
  ...flow.preview.pending.plugins.map(item => `插件 ${item.name} v${item.candidate.version}`),
  ...flow.preview.pending.personas.map(item => `角色 ${item.name}`),
])] : [])
const link = computed(() => flow.preview ? newPanelUrl() : null)
</script>
<template>
  <FormDialog v-model="flow.open" title="重启 LenBot" size="md" cancel-label="稍后重启" :busy="flow.busy || flow.waiting">
    <v-progress-linear v-if="flow.busy || flow.waiting" indeterminate color="primary" />
    <ErrorNote v-if="flow.error" title="重启没有完成" :error="flow.error" />
    <template v-if="flow.preview && flow.phase === 'preview'">
      <p>{{ pending.length ? `将应用这些已保存的修改：${pending.join('、')}。` : '没有待应用的修改，按当前配置重新启动。' }}</p>
<ErrorNote v-for="item in flow.preview.pending.plugins.filter(item => item.error)" :key="item.name" :title="`${item.name} 上次应用失败`" :error="item.error" />
      <p>重启后需要重新登录，页面上还没保存的修改会丢失。</p>
      <p v-if="flow.preview.chats.length">正在进行的聊天会先说完：{{ flow.preview.chats.map(sceneName).join('、') }}。</p>
      <div v-if="flow.preview.tasks.length">
        <p>这些任务会中断，重启后可以按原编号继续（账号浏览任务需要新建）：</p>
        <ul class="tasks"><li v-for="task in flow.preview.tasks" :key="task.id">#{{ task.id }} · {{ sceneName(task.scene) }} · {{ task.goal }}{{ task.account_browser ? '（账号浏览）' : '' }}</li></ul>
      </div>
      <p v-if="flow.preview.queued_tasks">排队中的 {{ flow.preview.queued_tasks }} 个任务会保留。</p>
      <p v-if="flow.preview.browsers.length">会关闭的账号浏览会话：{{ flow.preview.browsers.map(item => `#${item.id} · ${item.session || '创建中'}`).join('、') }}。</p>
      <p v-if="flow.preview.trials.length">会停止 {{ flow.preview.trials.length }} 个对话测试，记录保留。</p>
      <p v-if="panelChanged()">面板地址会变成：<a v-if="link" :href="link">{{ link }}</a><span v-else>见启动日志</span>。使用代理或 SSH 转发时要同步修改。</p>
      <v-alert v-if="!flow.preview.restartable" type="warning">当前不是用 <code>len-bot</code> 启动器运行的，面板不能重启。请停止后改用启动器运行。</v-alert>
    </template>
    <p v-if="flow.phase === 'waiting'">正在等待 LenBot 重新启动，完成后会自动打开登录页。</p>
    <p v-if="flow.phase === 'ready'">已重新启动，正在打开面板。</p>
    <template v-if="flow.phase === 'address-changed'">
      <p v-if="link">已开始重启。面板换了地址：<a :href="link">{{ link }}</a></p>
      <p v-else>已开始重启。面板的新地址见启动日志。</p>
    </template>
    <p v-if="flow.phase === 'unavailable' && flow.lastConnectionError" class="muted">还连不上：{{ flow.lastConnectionError.message }}</p>
    <template #actions>
      <v-btn v-if="flow.phase === 'unavailable'" color="primary" @click="waitForRestart">再次检查</v-btn>
      <v-btn v-if="flow.phase === 'preview'" color="primary" :disabled="flow.busy || !flow.preview?.restartable" @click="confirmRestart">确认重启</v-btn>
    </template>
  </FormDialog>
</template>
<style scoped>
p{overflow-wrap:anywhere}
.tasks{margin:var(--sp-1) 0 0;padding-left:var(--sp-5)}
</style>
