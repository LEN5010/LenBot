<script setup>
import { computed, ref, watch } from 'vue'
import { browserApi } from '../../api/browser.js'
import { taskStorageApi } from '../../api/taskStorage.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import ErrorNote from '../../components/ErrorNote.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import { finished } from './taskLabels.js'

const props = defineProps({ scene: String, task: Object, browser: Object, operator: String, configured: Boolean })
const emit = defineEmits(['changed'])
const status = useResource(() => browserApi.taskStatus(props.scene, props.task.id), { immediate: false })
const closing = useAction(), files = ref(false), resourcesVersion = ref(0)
const canClose = computed(() => props.configured && finished(props.task.status) && props.task.browser_active
  && props.task.browser_session && /^[1-9][0-9]*$/.test(props.operator))
watch(() => props.task.browser_active, () => { status.data.value = null })
async function close() {
  const result = await closing.run(() => taskStorageApi.close(props.scene, props.task.id, props.operator))
  if (!result) return
  status.data.value = null; notify('任务遗留浏览会话与执行环境已关闭，文件保留'); emit('changed')
}
function changed() { resourcesVersion.value++; emit('changed') }
</script>

<template>
  <section class="surface browser-task">
    <div class="head"><h2>{{ task.account_browser ? '账号浏览器' : '公共浏览器' }}</h2>
      <v-btn size="small" variant="text" @click="files = !files">{{ files ? '收起文件' : '浏览器文件' }} · 生成过 {{ browser.output_count }} 项</v-btn>
    </div>
    <p class="muted">属于任务 #{{ task.id }} · 发起人 QQ {{ task.requester }}</p>
    <template v-if="task.account_browser">
      <p>{{ task.browser_active ? (task.browser_session ? '任务仍持有账号会话' : '正在创建或等待确认实际会话') : browser.binding ? '任务会话已结束，产物保留' : '任务尚未建立会话' }}</p>
      <p v-if="browser.binding" class="muted">设备 {{ browser.binding.browser_instance_id }} · 会话 {{ browser.binding.session_id }}</p>
      <p v-if="task.question?.method === 'request_help'">正在等待你在专用浏览器完成：{{ task.question.title }}。完成后在浏览器的接手提示中继续。</p>
      <div class="row"><v-btn v-if="task.browser_active" size="small" variant="outlined" :loading="status.loading.value" @click="status.reload()">读取连接状态</v-btn>
        <v-btn v-if="canClose" size="small" variant="outlined" :loading="closing.busy.value" @click="close">关闭遗留会话</v-btn>
      </div>
      <p v-if="task.browser_active && status.data.value">上次读取：设备{{ status.data.value.browser ? '在线' : '离线' }} · {{ status.data.value.session ? '实际会话存在' : 'daemon 未列出此会话' }}<template v-if="status.data.value.session?.agent_window_id"> · 窗口 {{ status.data.value.session.agent_window_id }}</template></p>
      <p v-if="task.browser_active && !task.browser_session && finished(task.status)" class="muted">在能力页读取实际会话并选择释放。</p>
      <p class="muted">配对、设备授权和全局绑定在能力页管理。</p>
    </template>
    <template v-else>
      <p>{{ browser.public_enabled ? '当前工作配置可使用匿名公共浏览。' : '当前工作配置未启用公共浏览。' }}{{ task.container ? '任务容器正在运行，按工作需要打开页面。' : '当前没有任务容器。' }}</p>
      <p class="muted">公共浏览器随任务容器关闭；下载、截图和 PDF 保留在工作区。</p>
    </template>
    <ErrorNote v-if="status.error.value" title="读取浏览器状态失败" :error="status.error.value" />
    <ErrorNote v-if="closing.error.value" title="关闭失败" :error="closing.error.value" />
    <ResourceBrowser v-if="files && configured" :key="`${resourcesVersion}-${browser.output_count}`" :scene="scene" :task-id="task.id" :operator="operator"
      initial-path="out/browser" :selectable="false" @changed="changed" />
  </section>
</template>

<style scoped>
.browser-task{display:grid;gap:8px}.browser-task p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.head,.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.head{justify-content:space-between}
</style>
