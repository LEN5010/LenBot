<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, queryString } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify } from '../../store.js'
import { formatTime } from '../../time.js'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'
import TaskEvents from './TaskEvents.vue'
import TaskMore from './TaskMore.vue'
import { finished, taskStatus } from './taskLabels.js'

const props = defineProps({
  id: { type: Number, required: true }, scene: { type: String, required: true }, operator: { type: String, required: true },
  version: { type: Number, required: true }, service: { type: Object, required: true }, settings: { type: Object, default: null },
})
const emit = defineEmits(['dirty', 'changed'])
const detail = useResource(() => api(`/api/host/tasks/${props.id}?` + queryString({ scene: props.scene, after: 0, limit: 100 })))
watch(() => props.version, () => detail.reload())
const task = computed(() => detail.data.value?.task)
const at = value => formatTime(value, props.settings?.timezone)
const validQQ = computed(() => /^[1-9][0-9]*$/.test(props.operator))

const append = ref(''), followUp = ref(''), answer = ref(''), choice = ref(null)
const dirty = computed(() => Boolean(append.value || followUp.value || answer.value || choice.value !== null))
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
  if (action === 'cancel' && !window.confirm(`取消这个任务？\n${task.value.goal}`)) return
  if (typeof extra.confirmed === 'boolean' && !window.confirm(extra.confirmed ? '确定同意？' : '确定拒绝？')) return
  const result = await act.run(() => api('/api/host/tasks/action?' + queryString({ scene: props.scene }), { method: 'POST',
    body: JSON.stringify({ action, id: props.id, requester: props.operator, ...extra }) }))
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
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/host/tasks/${props.id}/live?` + queryString({ scene: props.scene }))
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
  const blob = await download.run(() => api(`/api/host/tasks/${props.id}/files/${file.id}?` + queryString({ scene: props.scene }), {}, 'blob'))
  if (!blob) return
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = file.name; document.body.appendChild(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const uploadLabel = { uploaded: '已发到 QQ', failed: '发送失败', unconfirmed: '不确定是否发出' }
const statusColor = { done: 'success', failed: 'error', waiting_input: 'warning' }
</script>

<template>
  <ErrorNote v-if="detail.error.value" title="读取任务失败" :error="detail.error.value" />
  <div v-if="task" class="task">
    <section class="surface">
      <div class="head">
        <h2>{{ task.goal }}</h2>
        <v-chip :color="statusColor[task.status]" variant="tonal">{{ taskStatus(task.status) }}</v-chip>
      </div>
      <p class="muted">QQ {{ task.requester }} 发起 · {{ at(task.created) }}{{ task.ended ? ` · ${at(task.ended)} 结束` : '' }}</p>
      <dl class="facts">
        <div><dt>交付什么</dt><dd>{{ task.deliverable }}</dd></div>
        <div v-if="task.context"><dt>补充说明</dt><dd>{{ task.context }}</dd></div>
        <div v-if="task.materials.length"><dt>给的资料</dt><dd>{{ task.materials.join('、') }}</dd></div>
        <div v-if="task.summary"><dt>结果</dt><dd>{{ task.summary }}</dd></div>
      </dl>
      <ErrorNote v-if="task.error" title="任务出错了" :error="task.error" />
      <DevOnly label="原始任务"><pre>{{ JSON.stringify({ ...task, network: detail.data.value.network }, null, 2) }}</pre></DevOnly>
    </section>

    <section v-if="running && (live?.preview || liveError || liveState === 'closed')" class="surface">
      <h2>正在写</h2>
      <pre v-if="live?.preview" class="preview">{{ live.preview.text }}</pre>
      <ErrorNote v-if="liveError" title="实时预览读不了" :error="liveError" />
      <p v-if="liveState === 'closed'" class="muted">实时预览断开了 <v-btn size="small" variant="text" @click="connectLive">重新连接</v-btn></p>
    </section>

    <section v-if="task.question" class="surface question">
      <h2>{{ task.question.method === 'request_help' ? '需要你在专用浏览器里帮忙' : '任务在问' }}</h2>
      <p class="text">{{ task.question.title }}</p>
      <p v-if="task.question.message" class="text">{{ task.question.message }}</p>
      <template v-if="canAnswer">
        <div v-if="task.question.method === 'confirm'" class="row">
          <v-btn color="primary" :disabled="!validQQ" :loading="act.busy.value" @click="submitAnswer(true)">同意</v-btn>
          <v-btn variant="outlined" :disabled="!validQQ || act.busy.value" @click="submitAnswer(false)">拒绝</v-btn>
        </div>
        <template v-else-if="task.question.method === 'select'">
          <v-radio-group v-model="choice" hide-details><v-radio v-for="option in task.question.options" :key="option" :label="option" :value="option" /></v-radio-group>
          <v-btn color="primary" :disabled="!validQQ || choice === null" :loading="act.busy.value" @click="submitAnswer()">回答</v-btn>
        </template>
        <template v-else>
          <v-textarea v-model="answer" label="你的回答" rows="2" auto-grow />
          <v-btn color="primary" :disabled="!validQQ" :loading="act.busy.value" @click="submitAnswer()">回答</v-btn>
        </template>
      </template>
    </section>

    <section v-if="canAppend || canContinue || canCancel" class="surface actions">
      <template v-if="canAppend">
        <v-textarea v-model="append" label="追加要求" rows="2" auto-grow hide-details />
        <v-btn variant="outlined" :disabled="!validQQ || !append.trim()" :loading="act.busy.value" @click="send('append', { text: append })">追加</v-btn>
      </template>
      <template v-if="canContinue">
        <v-textarea v-model="followUp" label="接着做" rows="2" auto-grow hint="在原来的基础上继续，例如“再加一张图表”" persistent-hint />
        <v-btn variant="outlined" :disabled="!validQQ || !followUp.trim()" :loading="act.busy.value" @click="send('continue', { text: followUp })">继续</v-btn>
      </template>
      <div v-if="canCancel"><v-btn variant="text" color="error" :disabled="!validQQ" :loading="act.busy.value" @click="send('cancel')">取消任务</v-btn></div>
      <p v-if="!validQQ" class="muted">填写上方你的 QQ 后才能操作。</p>
    </section>
    <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />

    <section v-if="detail.data.value.files.length" class="surface">
      <h2>交付的文件</h2>
      <ErrorNote v-if="download.error.value" title="下载失败" :error="download.error.value" />
      <ul class="files">
        <li v-for="file in detail.data.value.files" :key="file.id">
          <div><strong>{{ file.name }}</strong>
            <span class="muted"> · {{ (file.size / 1024).toFixed(1) }} KB{{ file.upload ? ` · ${uploadLabel[file.upload.status] || file.upload.status}` : '' }}</span>
            <p v-if="file.note" class="muted">{{ file.note }}</p>
            <ErrorNote v-if="file.upload?.error" title="发送到 QQ 失败" :error="file.upload.error" />
          </div>
          <v-btn size="small" variant="outlined" :loading="download.busy.value" @click="save(file)">下载</v-btn>
        </li>
      </ul>
    </section>

    <TaskEvents :scene="scene" :task-id="id" :first="detail.data.value.events" :first-next="detail.data.value.next_after" :timezone="settings?.timezone" />
    <TaskMore :scene="scene" :task="task" :files="detail.data.value.files" :service="service" :operator="operator" @changed="detail.reload(); emit('changed')" />
  </div>
</template>

<style scoped>
.task{display:grid;gap:16px;min-width:0}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.head h2{overflow-wrap:anywhere;white-space:pre-wrap}
.facts{display:grid;gap:10px;margin:12px 0 0}
.facts dt{font-size:13px;color:var(--muted)}
.facts dd{margin:2px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
.preview,.text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
.preview{max-height:24rem;overflow:auto;font-family:inherit}
.question,.actions{display:grid;gap:10px}
.row{display:flex;gap:8px}
.files{list-style:none;margin:0;padding:0;display:grid}
.files li{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:8px 0;border-bottom:1px solid var(--line)}
.files li:last-child{border-bottom:0}
.files p{margin:2px 0 0}
</style>
