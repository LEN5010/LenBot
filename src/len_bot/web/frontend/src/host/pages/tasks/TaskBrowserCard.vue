<script setup>
import { computed, ref, watch } from 'vue'
import { browserApi } from '../../api/browser.js'
import { taskStorageApi } from '../../api/taskStorage.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import Panel from '../../ui/Panel.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import { finished } from './taskLabels.js'

const props = defineProps({ scene: String, task: Object, browser: Object, operator: String, configured: Boolean })
const emit = defineEmits(['changed'])
const status = useResource(() => browserApi.taskStatus(props.scene, props.task.id), { immediate: false })
const closing = useAction(), files = ref(false), resourcesVersion = ref(0)
const canClose = computed(() => props.configured && finished(props.task.status) && props.task.browser_active
  && props.task.browser_session && /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))
watch(() => props.task.browser_active, () => { status.data.value = null })
async function close() {
  const result = await closing.run(() => taskStorageApi.close(props.scene, props.task.id, props.operator))
  if (!result) return
  status.data.value = null; notify('已关闭浏览会话和执行环境，文件保留'); emit('changed')
}
function changed() { resourcesVersion.value++; emit('changed') }
</script>

<template>
  <Panel :title="task.account_browser ? '账号浏览器' : '公共浏览器'">
    <template #actions><v-btn size="small" variant="text" @click="files = !files">{{ files ? '收起文件' : `浏览器文件 · ${browser.output_count} 项` }}</v-btn></template>
    <template v-if="task.account_browser">
      <p>{{ task.browser_active ? (task.browser_session ? '任务正在使用账号会话' : '正在创建或等待确认会话') : browser.binding ? '任务会话已结束，文件保留' : '任务还没有建立会话' }}</p>
      <p v-if="browser.binding" class="muted small">设备 {{ browser.binding.browser_instance_id }} · 会话 {{ browser.binding.session_id }}</p>
      <v-alert v-if="task.question?.method === 'request_help'" type="warning">需要你在专用浏览器里完成：{{ task.question.title }}。完成后在浏览器的接手提示里继续。</v-alert>
      <div class="inline"><v-btn v-if="task.browser_active" size="small" variant="outlined" :loading="status.loading.value" @click="status.reload()">查看连接状态</v-btn>
        <v-btn v-if="canClose" size="small" variant="outlined" :loading="closing.busy.value" @click="close">关闭遗留会话</v-btn>
      </div>
      <p v-if="task.browser_active && status.data.value" class="small">设备{{ status.data.value.browser ? '在线' : '离线' }} · {{ status.data.value.session ? '会话存在' : '找不到这个会话' }}<template v-if="status.data.value.session?.agent_window_id"> · 窗口 {{ status.data.value.session.agent_window_id }}</template></p>
      <p v-if="task.browser_active && !task.browser_session && finished(task.status)" class="muted small">可以在能力 › 独立任务里找到这个会话并关闭。</p>
    </template>
    <template v-else>
      <p>{{ browser.public_enabled ? '任务可以用公共浏览器打开网页。' : '任务没有开启公共浏览器。' }}{{ task.container ? '任务环境正在运行。' : '' }}</p>
      <p class="muted small">公共浏览器随任务环境关闭，下载、截图和 PDF 保留在工作区。</p>
    </template>
    <ErrorNote v-if="status.error.value" title="读取浏览器状态失败" :error="status.error.value" />
    <ErrorNote v-if="closing.error.value" title="关闭失败" :error="closing.error.value" />
    <ResourceBrowser v-if="files && configured" :key="`${resourcesVersion}-${browser.output_count}`" :scene="scene" :task-id="task.id" :operator="operator"
      initial-path="out/browser" :selectable="false" @changed="changed" />
  </Panel>
</template>

<style scoped>
p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
</style>
