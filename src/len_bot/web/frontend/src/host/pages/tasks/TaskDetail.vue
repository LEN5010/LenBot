<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { tasksApi } from '../../api/tasks.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { confirm } from '../../../composables/useConfirm.js'
import { formatTime } from '../../time.js'
import Panel from '../../ui/Panel.vue'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import FactList from '../../ui/FactList.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import CodeBlock from '../../ui/CodeBlock.vue'
import DevOnly from '../../ui/DevOnly.vue'
import TaskEvents from './TaskEvents.vue'
import TaskMore from './TaskMore.vue'
import TaskBrowserCard from './TaskBrowserCard.vue'
import ResourceBrowser from '../../components/ResourceBrowser.vue'
import ResourceTaskDraft from './ResourceTaskDraft.vue'
import { finished } from './taskLabels.js'

const props = defineProps({
  id: { type: Number, required: true }, scene: { type: String, required: true }, operator: { type: String, required: true },
  version: { type: Number, required: true }, service: { type: Object, required: true }, settings: { type: Object, default: null },
})
const emit = defineEmits(['dirty', 'changed', 'created'])
const detail = useResource(() => tasksApi.detail(props.scene, props.id))
watch(() => props.version, () => detail.reload())
const task = computed(() => detail.data.value?.task)
const resourceVersion = ref(0)
const at = value => formatTime(value, props.settings?.timezone)
const validIdentity = computed(() => /^[a-z][a-z0-9_-]*:[^:\s/\\]+$/.test(props.operator))

const append = ref(''), followUp = ref(''), answer = ref(''), choice = ref(null), resourceDirty = ref(false)
const dirty = computed(() => Boolean(append.value || followUp.value || answer.value || choice.value !== null || resourceDirty.value))
watch(dirty, value => emit('dirty', value), { immediate: true })
// An answer typed for one question must not be sent to the next one.
watch(() => task.value?.question?.id, (now, before) => { if (before !== undefined && now !== before) { answer.value = ''; choice.value = null } })

const canAppend = computed(() => props.service.accepting && ['running', 'waiting_input'].includes(task.value?.status))
const canContinue = computed(() => props.service.accepting && props.settings?.enabled && finished(task.value?.status)
  && !task.value.account_browser && !task.value.workspace_discard_requested)
const canAnswer = computed(() => props.service.accepting && task.value?.status === 'waiting_input'
  && task.value.question && task.value.question.method !== 'request_help')
const canCancel = computed(() => props.service.configured && ['queued', 'running', 'waiting_input'].includes(task.value?.status)
  && (props.service.accepting || task.value.status === 'queued'))

const act = useAction()
async function send(action, extra = {}) {
  if (action === 'cancel' && !await confirm({ title: '取消这个任务？', text: task.value.goal, confirmLabel: '取消任务', danger: true })) return
  if (typeof extra.confirmed === 'boolean' && !await confirm({ title: extra.confirmed ? '确定同意？' : '确定拒绝？', text: task.value.question.title,
    confirmLabel: extra.confirmed ? '同意' : '拒绝', danger: !extra.confirmed })) return
  const result = await act.run(() => tasksApi.action(props.scene, { action, id: props.id, requester: props.operator, ...extra }))
  if (!result) return
  if (action === 'append') append.value = ''
  if (action === 'continue') followUp.value = ''
  if (action === 'answer') { answer.value = ''; choice.value = null }
  notify({ append: '已追加', continue: '已继续', answer: '已回答', cancel: '已取消' }[action])
  detail.reload()
  emit('changed')
}
function submitAnswer(confirmed) {
  const question = task.value.question
  if (typeof confirmed === 'boolean') return send('answer', { question_id: question.id, confirmed })
  return send('answer', { question_id: question.id, text: question.method === 'select' ? choice.value : answer.value })
}

// The running assistant text, pushed over a WebSocket while the page is open.
const live = ref(null), liveState = ref('idle'), liveError = ref('')
let socket = null
function connectLive() {
  const connection = new WebSocket(tasksApi.liveUrl(props.scene, props.id))
  socket = connection
  liveState.value = 'connecting'
  connection.onopen = () => { if (socket === connection) liveState.value = 'connected' }
  connection.onmessage = event => {
    if (socket !== connection) return
    try { live.value = JSON.parse(event.data) } catch (error) { liveError.value = `${error.message}：${String(event.data).slice(0, 300)}`; connection.close() }
  }
  connection.onclose = () => { if (socket === connection) liveState.value = 'closed' }
}
function closeLive() { const connection = socket; socket = null; connection?.close() }
const running = computed(() => ['running', 'waiting_input'].includes(task.value?.status))
// Only a running task has text to stream, and only when the task service runs in this host.
watch(() => running.value && props.service.configured, on => { if (on && !socket) connectLive(); else if (!on) closeLive() }, { immediate: true })
onBeforeUnmount(closeLive)

const download = useAction()
async function save(file) {
  const blob = await download.run(() => tasksApi.download(props.scene, props.id, file.id))
  if (!blob) return
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = file.name; document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const facts = value => [['交付什么', value.deliverable], ['补充说明', value.context], ['给的资料', value.materials.join('、')], ['结果', value.summary]]
function created(task) {
  resourceDirty.value = false
  emit('dirty', dirty.value)
  emit('created', task)
}
</script>

<template>
  <ResourceState :resource="detail" error-title="读取任务失败">
  <div v-if="task" class="task">
    <Panel :title="task.goal" :description="`${task.requester} 发起 · ${at(task.created)}${task.ended ? ` · ${at(task.ended)} 结束` : ''}`">
      <template #actions><StatusBadge kind="task" :value="task.status" /></template>
      <FactList :items="facts(task)" class="facts" />
      <ErrorNote v-if="task.error" title="任务出错了" :error="task.error" />
      <DevOnly label="原始任务" :json="{ ...task, network: detail.data.value.network }" />
    </Panel>

    <Panel v-if="running && (live?.preview || liveError || liveState === 'closed')" title="正在写">
      <template v-if="liveState === 'closed'" #actions><v-btn size="small" variant="text" color="primary" @click="connectLive">重新连接</v-btn></template>
      <CodeBlock v-if="live?.preview" :text="live.preview.text" class="preview" />
      <ErrorNote v-if="liveError" title="实时预览读不了" :error="liveError" />
      <p v-if="liveState === 'closed'" class="muted">实时预览断开了。</p>
    </Panel>

    <Panel v-if="task.question" :title="task.question.method === 'request_help' ? '需要你在专用浏览器里帮忙' : '任务在问'" class="question">
      <p class="text">{{ task.question.title }}</p>
      <p v-if="task.question.message" class="text">{{ task.question.message }}</p>
      <template v-if="canAnswer">
        <div v-if="task.question.method === 'confirm'" class="inline">
          <v-btn color="primary" :disabled="!validIdentity" :loading="act.busy.value" @click="submitAnswer(true)">同意</v-btn>
          <v-btn variant="tonal" :disabled="!validIdentity || act.busy.value" @click="submitAnswer(false)">拒绝</v-btn>
        </div>
        <template v-else-if="task.question.method === 'select'">
          <v-radio-group v-model="choice" hide-details><v-radio v-for="option in task.question.options" :key="option" :label="option" :value="option" /></v-radio-group>
          <v-btn color="primary" class="start" :disabled="!validIdentity || choice === null" :loading="act.busy.value" @click="submitAnswer()">回答</v-btn>
        </template>
        <template v-else>
          <v-textarea v-model="answer" label="你的回答" rows="2" auto-grow />
          <v-btn color="primary" class="start" :disabled="!validIdentity" :loading="act.busy.value" @click="submitAnswer()">回答</v-btn>
        </template>
      </template>
    </Panel>

    <Panel v-if="canAppend || canContinue || canCancel" title="操作">
      <template v-if="canCancel" #actions><v-btn variant="text" color="error" size="small" :disabled="!validIdentity" :loading="act.busy.value" @click="send('cancel')">取消任务</v-btn></template>
      <div v-if="canAppend" class="compose">
        <v-textarea v-model="append" label="追加要求" rows="2" auto-grow />
        <v-btn variant="tonal" :disabled="!validIdentity || !append.trim()" :loading="act.busy.value" @click="send('append', { text: append })">追加</v-btn>
      </div>
      <div v-if="canContinue" class="compose">
        <v-textarea v-model="followUp" label="接着做" rows="2" auto-grow hint="在原来的基础上继续，例如“再加一张图表”" persistent-hint />
        <v-btn variant="tonal" :disabled="!validIdentity || !followUp.trim()" :loading="act.busy.value" @click="send('continue', { text: followUp })">继续</v-btn>
      </div>
      <p v-if="!validIdentity" class="muted small">在页面上方填写你的账号后才能操作。</p>
    </Panel>
    <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />

    <Panel v-if="detail.data.value.files.length" title="交付的文件">
      <ErrorNote v-if="download.error.value" title="下载失败" :error="download.error.value" />
      <ObjectList divided>
        <ObjectRow v-for="file in detail.data.value.files" :key="file.id" :title="file.name"
          :subtitle="`${(file.size / 1024).toFixed(1)} KB${file.note ? ` · ${file.note}` : ''}${file.exists ? '' : ' · 本地副本已不在'}`">
          <ErrorNote v-if="file.upload?.error" title="发送到 QQ 失败" :error="file.upload.error" />
          <template #meta><StatusBadge v-if="file.upload" kind="upload" :value="file.upload.status" /></template>
          <template #actions><v-btn size="small" variant="text" :disabled="!file.exists" :loading="download.busy.value" @click="save(file)">下载</v-btn></template>
        </ObjectRow>
      </ObjectList>
    </Panel>

    <TaskBrowserCard :scene="scene" :task="task" :browser="detail.data.value.browser" :operator="operator" :configured="service.configured"
      @changed="resourceVersion++; detail.reload(); emit('changed')" />
    <TaskEvents :scene="scene" :task-id="id" :first="detail.data.value.events" :first-next="detail.data.value.next_after" :timezone="settings?.timezone" />
    <TaskMore :scene="scene" :task="task" :files="detail.data.value.files" :service="service" :operator="operator" @changed="resourceVersion++; detail.reload(); emit('changed')" />
    <ResourceTaskDraft v-if="service.configured" :scene="scene" :operator="operator" @dirty="value => resourceDirty = value" @created="created">
      <template #default="{ select }">
        <ResourceBrowser :key="resourceVersion" :scene="scene" :task-id="id" :operator="operator" @select="select" @changed="detail.reload(); emit('changed')" />
      </template>
    </ResourceTaskDraft>
  </div>
  </ResourceState>
</template>

<style scoped>
.task{display:grid;gap:var(--sp-4);min-width:0}
.task p{margin:0}
.facts :deep(dd){white-space:pre-wrap}
.preview{max-height:24rem}
.text{white-space:pre-wrap;overflow-wrap:anywhere}
.start{justify-self:start}
.compose{display:grid;gap:var(--sp-2);justify-items:start}
.compose .v-input{width:100%}
</style>
