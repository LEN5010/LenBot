<script setup>
import { developerDetails } from '../composables/useDeveloperMode.js'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'
import TaskSkillCandidates from '../components/TaskSkillCandidates.vue'

const route = useRoute(), router = useRouter()
const state = ref(null), items = ref([]), nextOffset = ref(null), detail = ref(null)
const events = ref([]), nextAfter = ref(0), moreEvents = ref(false), fullEvents = ref({})
const stateLoading = ref(false), listLoading = ref(false), detailLoading = ref(false)
const eventReading = ref(null), fileReading = ref(null), creating = ref(false), acting = ref('')
const stateError = ref(''), listError = ref(''), detailError = ref(''), eventError = ref('')
const fileError = ref(''), downloadNotice = ref(''), createError = ref(''), actionError = ref('')
const createResult = ref(null), actionResult = ref(null), listStale = ref(false), detailStale = ref(false)
const socketState = ref('connecting'), socketError = ref(''), newData = ref(false), refreshing = ref(false), refreshError = ref('')
const liveSnapshot = ref(null), liveState = ref('idle'), liveError = ref(''), liveStale = ref(false)
const questionChanged = ref(false)
const createForm = ref({ requester: '', goal: '', deliverable: '', context: '', account_browser: false })
const operator = ref(''), appendText = ref(''), continueText = ref(''), answerText = ref(''), selectedAnswer = ref(null)
const detailHeading = ref(null)
const statusOptions = [
  { title: '进行中', value: 'active' }, { title: '全部', value: 'all' },
  ...['queued', 'running', 'waiting_input', 'done', 'failed', 'cancelled'].map(value =>
    ({ title: ({ queued:'排队中', running:'运行中', waiting_input:'等待输入', done:'已结束', failed:'失败', cancelled:'已取消' })[value], value })),
]
const selectedScene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : null)
const status = computed(() => typeof route.query.status === 'string' ? route.query.status : 'active')
const selectedId = computed(() => typeof route.query.id === 'string' ? route.query.id : null)
const sceneSettings = computed(() => state.value?.scenes.find(item => item.scene === selectedScene.value) || null)
const task = computed(() => detail.value?.task || null)
const canDownloadSession = computed(() => Boolean(state.value?.configured && task.value
  && ['done', 'failed', 'cancelled'].includes(task.value.status) && task.value.container === null))
const sessionHref = computed(() => `/api/host/tasks/${encodeURIComponent(selectedId.value)}/session?${new URLSearchParams({ scene:selectedScene.value })}`)
const acceptingScene = computed(() => state.value?.configured && state.value.accepting && sceneSettings.value?.enabled)
const canAppend = computed(() => state.value?.accepting && ['running','waiting_input'].includes(task.value?.status))
const canContinue = computed(() => !task.value?.account_browser && acceptingScene.value && ['done','failed','cancelled'].includes(task.value?.status))
const canAnswer = computed(() => state.value?.accepting && task.value?.status === 'waiting_input' && task.value.question)
const canCancel = computed(() => state.value?.configured && ['queued','running','waiting_input'].includes(task.value?.status)
  && (state.value.accepting || task.value.status === 'queued'))
const dirty = computed(() => Object.values(createForm.value).some(value => value !== '' && value !== false) || operator.value !== ''
  || appendText.value !== '' || continueText.value !== '' || answerText.value !== '' || selectedAnswer.value !== null)
useUnsavedChanges(dirty)
onBeforeRouteUpdate(to => {
  const changed = to.query.scene !== route.query.scene || to.query.status !== route.query.status || to.query.id !== route.query.id
  return !changed || !dirty.value || window.confirm('有未提交的任务或操作草稿。放弃这些内容并切换？')
})
const beginState = useRequestGuard()
const beginList = useRequestGuard(() => `${selectedScene.value}\u0000${status.value}`)
const beginDetail = useRequestGuard(() => `${selectedScene.value}\u0000${selectedId.value}`)
let eventEpoch = 0
const beginEvent = useRequestGuard(() => `${selectedScene.value}\u0000${selectedId.value}\u0000${eventEpoch}`)
const beginCreate = useRequestGuard(() => selectedScene.value)
const beginAction = useRequestGuard(() => `${selectedScene.value}\u0000${selectedId.value}`)
const beginFile = useRequestGuard(() => `${selectedScene.value}\u0000${selectedId.value}`)
const beginSnapshot = useRequestGuard(() => `${selectedScene.value}\u0000${status.value}\u0000${selectedId.value}`)
let active = true, socket = null, refreshPending = false
let liveSocket = null, liveMounted = false
function localTime(value) {
  if (value === null || value === undefined || !sceneSettings.value?.timezone) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    hour12: false, timeZone: sceneSettings.value.timezone, timeZoneName: 'short',
  })
}
function statusLabel(value) { return ({ queued:'排队中', running:'运行中', waiting_input:'等待输入', done:'正常结束', failed:'失败', cancelled:'已取消' })[value] || value }
function byteCount(value) { return value === null ? '未设置' : `${Number(value).toLocaleString('zh-CN')} 字节` }
function uploadLabel(value) {
  return ({ uploaded:'平台 API 已确认上传；不代表客户端已收到', failed:'最近一次上传失败', unconfirmed:'最近一次上传结果未确认' })[value]
}
function errorMessage(error, verb) {
  return error.status >= 400 && error.status < 500
    ? `${verb}未被接受：${error.message}`
    : `${verb}结果未确认：${error.message} 草稿已保留；请手动重读任务，不会自动重试。`
}
function clearActionInputs() {
  operator.value = ''; appendText.value = ''; continueText.value = ''; answerText.value = ''; selectedAnswer.value = null
}
function resetActionDrafts() {
  clearActionInputs()
  questionChanged.value = false
  actionError.value = ''; actionResult.value = null; acting.value = ''; detailStale.value = false
}
function discardOldAnswer() {
  answerText.value = ''; selectedAnswer.value = null; questionChanged.value = false; actionError.value = ''
}
function resetDetail() {
  ++eventEpoch
  detail.value = null; events.value = []; nextAfter.value = 0; moreEvents.value = false; fullEvents.value = {}
  detailLoading.value = false; eventReading.value = null; fileReading.value = null
  detailError.value = ''; eventError.value = ''; fileError.value = ''; downloadNotice.value = ''
  resetActionDrafts()
}
function resetList() {
  items.value = []; nextOffset.value = null; listLoading.value = false; listError.value = ''; listStale.value = false
  resetDetail()
  createForm.value = { requester: '', goal: '', deliverable: '', context: '', account_browser: false }
  createError.value = ''; createResult.value = null; creating.value = false
}
async function readState() {
  const fresh = beginState()
  stateLoading.value = true
  try {
    const value = await api('/api/host/tasks/state')
    if (!fresh()) return
    state.value = value; stateError.value = ''
    if (selectedScene.value === null && value.scenes.length) {
      await router.replace({ name: 'host-tasks', query: { scene: value.scenes[0].scene, status: status.value } })
    } else if (selectedScene.value) {
      await readList(false)
      if (selectedId.value) await readDetail(false)
    }
  } catch (error) { if (fresh()) stateError.value = error.message }
  finally { if (fresh()) stateLoading.value = false }
}
async function readList(more = false) {
  if (!selectedScene.value || (more && (nextOffset.value === null || listLoading.value || refreshing.value))) return
  const name = selectedScene.value, filter = status.value, offset = more ? nextOffset.value : 0
  const fresh = beginList()
  listLoading.value = true
  try {
    const result = await api(`/api/host/tasks?${new URLSearchParams({scene:name,status:filter,offset:String(offset),limit:'20'})}`)
    if (!fresh()) return
    items.value = more ? [...items.value, ...result.items] : result.items
    nextOffset.value = result.next_offset; listError.value = ''; listStale.value = false
  } catch (error) { if (fresh()) listError.value = error.message }
  finally { if (fresh()) listLoading.value = false }
}
async function readDetail(more = false) {
  if (more && (dirty.value || detailLoading.value || refreshing.value)) return
  if (!selectedScene.value || !selectedId.value || !/^[1-9][0-9]*$/.test(selectedId.value)) {
    if (selectedId.value) detailError.value = '任务地址中的 id 须为实际正整数。'
    return
  }
  const name = selectedScene.value, id = selectedId.value, after = more ? nextAfter.value : 0
  const fresh = beginDetail()
  detailLoading.value = true
  if (!more) { ++eventEpoch; eventReading.value = null; eventError.value = '' }
  try {
    const result = await api(`/api/host/tasks/${encodeURIComponent(id)}?${new URLSearchParams({scene:name,after:String(after),limit:'20'})}`)
    if (!fresh()) return
    detail.value = { task: result.task, files: result.files, network: result.network }
    events.value = more ? [...events.value, ...result.events] : result.events
    if (!more) fullEvents.value = {}
    nextAfter.value = result.next_after
    moreEvents.value = result.events.length === 20 && result.next_after !== null
    detailError.value = ''; detailStale.value = false
    if (!more) { await nextTick(); detailHeading.value?.focus({ preventScroll:true }) }
  } catch (error) { if (fresh()) detailError.value = error.message }
  finally { if (fresh()) detailLoading.value = false }
}
// Read each page in order. A live notice never discards event pages the operator
// has already opened, nor does it refetch large event bodies.
async function readSnapshotPages(name, filter, id) {
  const wantedItems = Math.max(20, items.value.length)
  const list = [], wantedEvents = Math.max(20, events.value.length)
  let offset = 0, listNext = null
  do {
    const page = await api(`/api/host/tasks?${new URLSearchParams({scene:name,status:filter,offset:String(offset),limit:'20'})}`)
    list.push(...page.items)
    listNext = page.next_offset
    offset = listNext
  } while (listNext !== null && list.length < wantedItems)
  if (!id) return { list, listNext, detail: null }
  let after = 0, page, previews = []
  do {
    page = await api(`/api/host/tasks/${encodeURIComponent(id)}?${new URLSearchParams({scene:name,after:String(after),limit:'20'})}`)
    previews.push(...page.events)
    after = page.next_after
  } while (page.events.length === 20 && after !== null && previews.length < wantedEvents)
  return { list, listNext, detail: {
    task: page.task, files: page.files, network: page.network, events: previews, nextAfter: after,
    moreEvents: page.events.length === 20 && after !== null,
  } }
}
async function drainRefresh(manual = false) {
  if (refreshing.value) return
  refreshing.value = true
  try {
    while (active && refreshPending) {
      if ((!manual && dirty.value) || creating.value || acting.value || listLoading.value || detailLoading.value) return
      refreshPending = false
      const fresh = beginSnapshot(), name = selectedScene.value, filter = status.value, id = selectedId.value
      const actionAtStart = JSON.stringify([operator.value, appendText.value, continueText.value, answerText.value, selectedAnswer.value])
      try {
        const latestState = await api('/api/host/tasks/state')
        const pages = name ? await readSnapshotPages(name, filter, id) : null
        if (!fresh()) continue
        const actionNow = JSON.stringify([operator.value, appendText.value, continueText.value, answerText.value, selectedAnswer.value])
        if ((!manual && dirty.value) || (manual && actionNow !== actionAtStart) || creating.value || acting.value) {
          newData.value = true
          return
        }
        state.value = latestState
        if (pages) {
          items.value = pages.list; nextOffset.value = pages.listNext; listError.value = ''; listStale.value = false
          if (pages.detail) {
            if (manual && (answerText.value !== '' || selectedAnswer.value !== null)
                && task.value?.question?.id !== pages.detail.task.question?.id) questionChanged.value = true
            detail.value = { task:pages.detail.task, files:pages.detail.files, network:pages.detail.network }
            events.value = pages.detail.events; nextAfter.value = pages.detail.nextAfter
            moreEvents.value = pages.detail.moreEvents; detailError.value = ''; detailStale.value = false
          }
        }
        stateError.value = ''; refreshError.value = ''; newData.value = refreshPending
        if (manual && !questionChanged.value) actionError.value = ''
      } catch (error) {
        if (fresh()) { refreshError.value = error.message; newData.value = true }
        return
      }
    }
  } finally { refreshing.value = false }
}
function queueRefresh() {
  newData.value = true
  refreshPending = true
  if (!dirty.value && !creating.value && !acting.value && !listLoading.value && !detailLoading.value) void drainRefresh()
}
async function manualRefresh() {
  if (refreshing.value || creating.value || acting.value || listLoading.value || detailLoading.value) return
  if (dirty.value && !window.confirm('有未提交草稿。重读会保留输入；若待答问题已变化，原回答不能直接用于新问题。继续？')) return
  refreshPending = true; newData.value = true
  await drainRefresh(true)
}
function connect() {
  if (socket && socket.readyState <= WebSocket.OPEN) return
  socketError.value = ''; socketState.value = 'connecting'
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/host/events`)
  socket = connection
  connection.onopen = () => { if (active && socket === connection) socketState.value = 'connected' }
  connection.onmessage = event => {
    if (!active || socket !== connection) return
    try {
      if (JSON.parse(event.data)?.type !== 'changed') throw new Error('未知的变化通知')
      queueRefresh()
    } catch (error) {
      socketError.value = `变化通知无法读取：${error.message}`
      connection.close()
    }
  }
  connection.onerror = () => {
    if (active && socket === connection) socketError.value = '页面实时连接出错；不会自动重连。'
  }
  connection.onclose = () => { if (active && socket === connection) socketState.value = 'disconnected' }
}
function reconnect() { socket?.close(); socket = null; connect() }
function closeLive(clear = false) {
  liveSocket?.close()
  liveSocket = null
  if (clear) {
    liveSnapshot.value = null; liveError.value = ''; liveStale.value = false; liveState.value = 'idle'
  }
}
function connectLive() {
  const name = selectedScene.value, id = selectedId.value
  if (!liveMounted || !name || !id || !/^[1-9][0-9]*$/.test(id)) return
  if (liveSocket && liveSocket.readyState <= WebSocket.OPEN) return
  liveState.value = 'connecting'; liveError.value = ''; liveStale.value = liveSnapshot.value !== null
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const connection = new WebSocket(`${protocol}//${location.host}/api/host/tasks/${encodeURIComponent(id)}/live?${new URLSearchParams({scene:name})}`)
  liveSocket = connection
  connection.onopen = () => { if (active && liveSocket === connection) liveState.value = 'connected' }
  connection.onmessage = event => {
    if (!active || liveSocket !== connection || selectedScene.value !== name || selectedId.value !== id) return
    try {
      const value = JSON.parse(event.data)
      if (value?.task_id !== Number(id) || value.scene !== name || typeof value.status !== 'string'
          || (value.preview !== null && (typeof value.preview?.text !== 'string'
            || typeof value.preview.truncated !== 'boolean' || typeof value.preview.complete !== 'boolean'))) {
        throw new Error(`任务预览字段不匹配：${String(event.data).slice(0, 300)}`)
      }
      liveSnapshot.value = value; liveStale.value = false; liveError.value = ''
    } catch (error) {
      liveError.value = `任务文字预览无法读取：${error.message}`
      connection.close()
    }
  }
  connection.onerror = () => {
    if (active && liveSocket === connection) liveError.value = '任务文字预览连接出错；不会自动重连。'
  }
  connection.onclose = event => {
    if (!active || liveSocket !== connection) return
    liveState.value = 'disconnected'; liveStale.value = liveSnapshot.value !== null
    if (!liveError.value && event.reason) liveError.value = `连接已关闭：${event.reason}`
  }
}
function reconnectLive() { closeLive(); connectLive() }
function eventContent(record) {
  const body = record.body
  const content = body.type === 'tool_execution_end' ? body.result?.content
    : body.type === 'message_end' ? body.message?.content : null
  return Array.isArray(content) ? content.filter(part => part.type === 'text'
    || (part.type === 'image' && ['image/png','image/jpeg','image/webp'].includes(part.mimeType))) : []
}
function eventLabel(event) {
  const labels = {tool_execution_start:'开始工具操作', tool_execution_end:'工具操作结果',
    message_start:'开始生成', message_end:'生成结束', agent_start:'开始执行', agent_end:'本次执行结束',
    extension_ui_request:'等待补充或确认', finished:'任务结束', input:'新增要求',
    question:'向请求人提问', answer:'收到回答', browser_started:'专用浏览器会话已建立',
    browser_stopped:'专用浏览器会话已关闭', browser_released:'残留会话已清理'}
  return labels[event.event_type] || '任务记录'
}
async function readEvent(event) {
  if (eventReading.value !== null) return
  const name = selectedScene.value, id = selectedId.value, fresh = beginEvent()
  eventReading.value = event.id; eventError.value = ''
  try {
    const result = await api(`/api/host/tasks/${encodeURIComponent(id)}/events/${encodeURIComponent(event.id)}?${new URLSearchParams({scene:name})}`)
    if (fresh()) fullEvents.value = { ...fullEvents.value, [event.id]: { record: result, readAt: Date.now() / 1000 } }
  } catch (error) { if (fresh()) eventError.value = error.message }
  finally { if (fresh()) eventReading.value = null }
}
function navigate(query) { router.push({ name:'host-tasks', query }) }
function selectScene(value) { navigate({ scene:value, status:status.value }) }
function selectStatus(value) { navigate({ scene:selectedScene.value, status:value }) }
function selectTask(id) { navigate({ scene:selectedScene.value, status:status.value, id:String(id) }) }
async function createTask() {
  if (!acceptingScene.value || creating.value) return
  const name = selectedScene.value, payload = { ...createForm.value }, fresh = beginCreate()
  creating.value = true; createError.value = ''; createResult.value = null
  try {
    const result = await api(`/api/host/tasks/delegate?${new URLSearchParams({scene:name})}`, { method:'POST', body:JSON.stringify(payload) })
    if (!fresh()) return
    createResult.value = result; listStale.value = true
    createForm.value = { requester:'', goal:'', deliverable:'', context:'', account_browser:false }
  } catch (error) { if (fresh()) createError.value = errorMessage(error, '新建任务') }
  finally { if (fresh()) creating.value = false }
}
async function submitAction(action, confirmed) {
  if (!task.value || !operator.value || acting.value) return
  if (action === 'answer' && questionChanged.value) {
    actionError.value = '待答问题已变化。原回答草稿仍保留；请先舍弃旧答案，再核对当前问题。'
    return
  }
  if (action === 'cancel' && !window.confirm(`确认取消当前场景任务 ${task.value.id}：${task.value.goal}？`)) return
  if (action === 'answer' && typeof confirmed === 'boolean'
      && !window.confirm(`以实际操作者 QQ ${operator.value} ${confirmed?'明确同意':'明确拒绝'}当前操作确认？`)) return
  const payload = { action, id:task.value.id, requester:operator.value }
  if (action === 'append') payload.text = appendText.value
  if (action === 'continue') payload.text = continueText.value
  if (action === 'answer') {
    payload.question_id = task.value.question.id
    if (typeof confirmed === 'boolean') payload.confirmed = confirmed
    else payload.text = task.value.question.method === 'select' ? selectedAnswer.value : answerText.value
  }
  const name = selectedScene.value, fresh = beginAction()
  acting.value = action; actionError.value = ''; actionResult.value = null
  try {
    const result = await api(`/api/host/tasks/action?${new URLSearchParams({scene:name})}`, { method:'POST', body:JSON.stringify(payload) })
    if (!fresh()) return
    actionResult.value = { action, result }; detailStale.value = true; listStale.value = true
    clearActionInputs(); questionChanged.value = false
  } catch (error) {
    if (fresh()) {
      if (error.status === 409) {
        newData.value = true
        actionError.value = `当前问题或任务状态已变化：${error.message} 草稿已保留；请手动重读并重新确认，不会自动改问题或重发。`
      } else actionError.value = errorMessage(error, '任务操作')
    }
  }
  finally { if (fresh()) acting.value = '' }
}
async function downloadFile(file) {
  if (!task.value || fileReading.value !== null) return
  const name = selectedScene.value, id = selectedId.value, fresh = beginFile()
  fileReading.value = file.id; fileError.value = ''; downloadNotice.value = ''
  try {
    const response = await fetch(`/api/host/tasks/${encodeURIComponent(id)}/files/${encodeURIComponent(file.id)}?${new URLSearchParams({scene:name})}`, { credentials:'same-origin' })
    if (!response.ok) throw new Error(`HTTP ${response.status}：${await response.text()}`)
    const blob = await response.blob()
    if (!fresh()) return
    const url = URL.createObjectURL(blob)
    try {
      const link = document.createElement('a')
      link.href = url; link.download = file.name; document.body.appendChild(link); link.click(); link.remove()
      downloadNotice.value = `已向浏览器发起 ${file.name} 的副本下载；本次下载不代表已保存到本机，也不会触发新的 QQ 上传。`
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
  } catch (error) { if (fresh()) fileError.value = error.message }
  finally { if (fresh()) fileReading.value = null }
}
watch([selectedScene, status], () => {
  newData.value = false; refreshError.value = ''; refreshPending = false
  resetList()
  if (state.value && selectedScene.value) readList(false)
  if (state.value && selectedId.value) readDetail(false)
})
watch(selectedId, () => {
  newData.value = false; refreshError.value = ''; refreshPending = false
  resetDetail()
  if (state.value && selectedId.value) readDetail(false)
})
watch([selectedScene, selectedId], () => { closeLive(true); if (liveMounted) connectLive() })
onMounted(async () => { liveMounted = true; connectLive(); await readState(); if (active) connect() })
onBeforeUnmount(() => { active = false; liveMounted = false; socket?.close(); closeLive() })
</script>

<template>
  <div class="page-stack host-tasks">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>独立任务</h1>
      <p class="muted">查看和操作已保存任务。任务入队不代表开始执行，正常结束不等于目标已满足；文件复制登记不等于已上传 QQ。面板登录账户不替代真实 QQ 操作人。</p></div>
      <v-btn variant="outlined" :loading="stateLoading || refreshing" :disabled="creating || Boolean(acting)" @click="manualRefresh">手动重读任务快照</v-btn></header>
    <v-alert v-if="socketState==='disconnected' || socketError" type="warning" variant="tonal" role="alert">
      页面实时更新已断开；下方仍是上次读取的记录，不会自动重连。{{ socketError }}
      <div class="mt-2"><v-btn variant="outlined" @click="reconnect">重新连接实时更新</v-btn></div>
    </v-alert>
    <p v-else class="muted" role="status">{{ socketState==='connected'?'任务变化通知已连接':'正在连接任务变化通知…' }}</p>
    <v-alert v-if="newData" type="warning" variant="tonal" role="status">
      收到宿主变化通知；当前任务和问题可能仍是旧快照，草稿不会被后台刷新覆盖。可手动重读；若直接操作，仍使用画面所示任务和问题，由服务端校验当前状态。
      <div class="mt-2"><v-btn variant="outlined" :loading="refreshing" :disabled="creating || Boolean(acting)" @click="manualRefresh">手动重读最新快照</v-btn></div>
    </v-alert>
    <v-alert v-if="refreshError" type="error" variant="tonal" role="alert">快照读取失败，保留上次结果：{{ refreshError }}</v-alert>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'状态读取失败 · 保留上次结果':'状态读取失败'">{{ stateError }}</v-alert>
    <div v-if="stateLoading && !state" class="surface empty-state" role="status">正在读取任务服务与已配置场景…</div>
    <section v-if="state" class="surface"><div class="section-heading"><h2>服务现场 · 最近读取</h2><v-chip variant="tonal" :color="socketState==='connected' && !newData && state.accepting?'success':'warning'">{{ !state.configured?'未配置':state.accepting?'最近读取：接受任务':'最近读取：不接受新操作' }}</v-chip></div>
      <p class="muted">{{ state.notice }}；公共联网代理：{{ state.public_network?'配置已启用，未据此验证域名连通':'配置未启用' }}；时间按 {{ sceneSettings?.timezone ?? '所选场景时区' }} 显示。</p>
      <p class="muted">任务文件上传出口：{{ state.file_upload?'当前已配置；挂载是否可读及每份文件上传仍须实际回执确认':'当前未配置' }}。</p>
      <v-alert v-if="state.error" type="error" variant="tonal" role="alert">执行器错误原文：{{ state.error }}</v-alert>
      <div v-if="sceneSettings" class="scene-facts"><strong>{{ sceneName(sceneSettings.scene) }}</strong><span>此场景任务：{{ sceneSettings.enabled?'开放':'未开放' }}</span>
        <span>并行上限 {{ sceneSettings.max_running }} · 每人每日上限 {{ sceneSettings.max_daily_tasks }}</span></div>
      <div v-if="sceneSettings?.network_today" class="network-facts">
        <strong>本场景当日代理计量 · {{ sceneSettings.network_today.date }}</strong>
        <span>{{ sceneSettings.network_today.enabled?'配置启用':'配置未启用' }} · 上行 {{ byteCount(sceneSettings.network_today.up) }} · 下行 {{ byteCount(sceneSettings.network_today.down) }}</span>
        <span>限额 {{ byteCount(sceneSettings.network_today.limit) }} · 本场景历史中断连接 {{ sceneSettings.network_today.incomplete_connections }}</span>
        <p v-if="sceneSettings.network_today.last_error" class="original-text">最近保存的连接错误（{{ localTime(sceneSettings.network_today.last_error.at) }}）：{{ sceneSettings.network_today.last_error.message }}</p>
      </div>
      <p v-if="!state.configured" class="muted">尚未配置任务执行器。可只读查看已有任务记录；不能在这里假启动容器。</p>
    </section>
    <section v-if="state" class="surface"><h2>选择场景与列表范围</h2><div class="form-grid">
      <v-select :model-value="selectedScene" :items="state.scenes.map(item=>({title:sceneName(item.scene),value:item.scene}))" label="场景" hide-details="auto" @update:model-value="selectScene" />
      <v-select :model-value="status" :items="statusOptions" label="任务状态" hide-details="auto" @update:model-value="selectStatus" /></div></section>
    <section v-if="state && selectedScene" class="surface"><div class="section-heading"><h2>新建独立任务</h2><span class="muted">必须明确填写实际请求人 QQ</span></div>
      <p v-if="!acceptingScene" class="muted">{{ !state.configured?'执行器未配置':!state.accepting?'执行器当前不接受新任务':'此场景未开放任务执行' }}；草稿不会自动提交。</p>
      <form @submit.prevent="createTask"><fieldset :disabled="!acceptingScene || creating" class="editor-grid">
        <v-text-field v-model="createForm.requester" label="实际请求人 QQ" inputmode="numeric" hide-details="auto" />
        <v-textarea v-model="createForm.goal" label="任务原目标" rows="3" auto-grow hide-details="auto" />
        <v-textarea v-model="createForm.deliverable" label="期望交付物" rows="3" auto-grow hide-details="auto" />
        <v-textarea v-model="createForm.context" label="补充上下文（可空）" rows="3" auto-grow hide-details="auto" />
        <v-checkbox v-model="createForm.account_browser" label="主人已明确同意此次专用账号浏览任务（独立工作区，不续接）" hide-details /></fieldset>
        <div class="form-actions"><v-btn type="submit" color="primary" :loading="creating" :disabled="!acceptingScene || !createForm.requester || !createForm.goal.trim() || !createForm.deliverable.trim()">登记并排队</v-btn>
          <span class="muted">创建成功仅说明真实任务已排队，不表示模型已运行。</span></div></form>
      <v-alert v-if="createError" type="error" variant="tonal" role="alert">{{ createError }}</v-alert>
      <v-alert v-if="createResult" type="success" variant="tonal" role="status">后端返回任务 {{ createResult.id }} · {{ statusLabel(createResult.status) }}。列表尚未重读；请手动刷新确认后续状态。</v-alert>
    </section>
    <section v-if="selectedScene" class="surface"><div class="section-heading"><h2>任务列表</h2><v-btn variant="outlined" :loading="refreshing" @click="manualRefresh">重读列表</v-btn></div>
      <p v-if="listStale" class="muted">任务操作后此列表尚未重新读取；下方保留上次结果。</p>
      <v-alert v-if="listError" type="error" variant="tonal" role="alert" :title="items.length?'列表读取失败 · 保留上次结果':'列表读取失败'">{{ listError }}</v-alert>
      <p v-if="listLoading && !items.length" role="status" class="muted">正在读取任务…</p>
      <p v-else-if="!items.length && !listError" class="muted">此场景和状态范围没有已保存任务。</p>
      <ul v-if="items.length" class="task-list"><li v-for="item in items" :key="item.id" :class="{selected:String(item.id)===selectedId}">
        <v-btn variant="text" @click="selectTask(item.id)"><strong>{{ item.goal }}</strong></v-btn>
        <p class="muted">任务 {{ item.id }} · {{ statusLabel(item.status) }} · 请求人 QQ {{ item.requester }} · {{ localTime(item.created) }}</p>
        <p class="original-text">交付物：{{ item.deliverable }}</p></li></ul>
      <v-btn v-if="nextOffset!==null" variant="outlined" :loading="listLoading" :disabled="listLoading || refreshing" @click="readList(true)">读取下一页（20项）</v-btn>
    </section>
    <section v-if="selectedId" class="surface" aria-labelledby="task-detail-title"><div class="section-heading"><h2 id="task-detail-title" ref="detailHeading" tabindex="-1">任务详情</h2>
        <v-btn variant="outlined" :loading="refreshing" @click="manualRefresh">重读详情</v-btn></div>
      <v-alert v-if="detailError" type="error" variant="tonal" role="alert" :title="detail?'详情读取失败 · 保留上次结果':'详情读取失败'">{{ detailError }}</v-alert>
      <p v-if="detailLoading && !detail" role="status" class="muted">正在读取原任务与事件预览…</p>
      <section class="live-preview" aria-labelledby="task-live-title">
        <div class="section-heading"><h3 id="task-live-title">当前助手文字预览</h3>
          <v-btn v-if="liveState==='disconnected'" variant="outlined" @click="reconnectLive">重新连接文字预览</v-btn></div>
        <p class="muted">{{ liveState==='connected'?'预览连接已建立':liveState==='connecting'?'正在连接文字预览…':liveState==='disconnected'?'预览连接已断开；不会自动重连':'尚未连接文字预览' }}<template v-if="liveStale"> · 下方保留的是断线前快照</template></p>
        <p v-if="liveError" class="original-text">{{ liveError }}</p>
        <p v-if="liveSnapshot" class="muted">最近收到的任务状态：{{ statusLabel(liveSnapshot.status) }}</p>
        <p v-if="liveSnapshot?.preview===null" class="muted">当前没有助手文字预览；可在下方读取已保存事件。</p>
        <pre v-else-if="liveSnapshot?.preview" aria-label="当前助手文字预览正文">{{ liveSnapshot.preview.text }}</pre>
        <p v-else class="muted">{{ liveState==='disconnected'?'断线前未取得文字预览。':'等待首个文字预览快照…' }}</p>
        <p v-if="liveSnapshot?.preview" class="muted">{{ liveSnapshot.preview.complete?'该条助手消息已结束，不代表任务完成。':'该条助手消息尚未结束。' }}{{ liveSnapshot.preview.truncated?' 预览已截短。':'' }}完整原文请按需读取下方事件；任务结束后可下载原生会话。</p>
      </section>
      <template v-if="detail"><p v-if="detailStale" class="muted">操作返回后，当前详情尚未重读；不能将下方旧状态当作最新结果。</p>
        <div class="task-header"><strong>{{ task.goal }}</strong><v-chip variant="tonal" :color="task.status==='failed'?'error':task.status==='done'?'success':'info'">{{ statusLabel(task.status) }}</v-chip></div>
        <dl class="task-facts"><div><dt>任务记录</dt><dd>{{ task.id }} · {{ sceneName(task.scene) }}</dd></div><div><dt>实际请求人</dt><dd>QQ {{ task.requester }}</dd></div>
          <div><dt>创建 / 开始 / 结束</dt><dd>{{ localTime(task.created) }} / {{ localTime(task.started) }} / {{ localTime(task.ended) }}</dd></div>
          <div><dt>期望交付物</dt><dd class="original-text">{{ task.deliverable }}</dd></div>
          <div><dt>补充上下文</dt><dd class="original-text">{{ task.context || '未填写' }}</dd></div>
          <div v-if="task.summary!==null"><dt>执行总结</dt><dd class="original-text">{{ task.summary }}</dd></div></dl>
        <div class="session-download"><a v-if="canDownloadSession" :href="sessionHref" target="_blank" rel="noopener noreferrer" class="session-link">下载原生 Pi 会话</a>
          <p v-else class="muted">运行中不可下载；仅已结束、容器已停止且配置了 worker 的任务可读取原生会话。</p>
          <p class="muted">下载由浏览器处理，读取失败将在新页显示。下载不是锁定快照；同时续接会话可能改变原文件。</p></div>
        <section v-if="detail.network" class="network-facts"><h3>本任务代理计量</h3>
          <p>{{ detail.network.enabled?'配置启用':'配置未启用' }} · 上行 {{ byteCount(detail.network.up) }} · 下行 {{ byteCount(detail.network.down) }}</p>
          <p>限额 {{ byteCount(detail.network.limit) }} · 中断连接 {{ detail.network.incomplete_connections }}</p>
          <p v-if="detail.network.last_error" class="original-text">最近保存的连接错误（{{ localTime(detail.network.last_error.at) }}）：{{ detail.network.last_error.message }}</p>
        </section>
        <p v-if="sceneSettings?.network_today || detail.network" class="muted">这些是代理进程内实时计量、按连接合并保存的已知字节；异常退出可能丢失未存尾部，不是跨断电精确硬封顶，也不表示远端已收到。</p>
        <v-alert v-if="task.error" type="error" variant="tonal" role="alert">任务错误原文：<pre>{{ task.error }}</pre></v-alert>
        <details><summary>查看原始任务输入</summary><pre>{{ task.input }}</pre></details>
        <section v-if="task.question" class="question"><h3>当前等待的问题</h3><p class="original-text">{{ task.question.title }}</p>
          <p v-if="task.question.message" class="original-text">{{ task.question.message }}</p>
          <p v-if="task.question.method==='select'" class="muted">可选回答：{{ task.question.options.join('、') }}</p>
          <p class="muted">问题类型：{{ task.question.method }}。普通补充信息由执行过程理解；面板不会替模型证明是谁应回答。操作确认则必须由有权限的实际 QQ 明确同意或拒绝。</p></section>
        <section class="task-actions"><h3>真实操作者</h3><v-text-field v-model="operator" label="本次操作人的实际 QQ（必填，不预填请求人）" inputmode="numeric" hide-details="auto" />
          <v-alert v-if="questionChanged" type="warning" variant="tonal" role="alert">重读后待答问题已变化；原回答草稿保留，但不会用于新问题。
            <div class="mt-2"><v-btn variant="outlined" @click="discardOldAnswer">舍弃旧答案并核对当前问题</v-btn></div>
          </v-alert>
          <p v-if="!state?.accepting" class="muted">当前执行器不接受追加、续接或回答；已排队任务仍可尝试明确取消。</p>
          <form v-if="canAppend && !detailStale" @submit.prevent="submitAction('append')"><v-textarea v-model="appendText" label="追加运行中要求" rows="2" auto-grow hide-details="auto" />
            <v-btn type="submit" variant="outlined" :loading="acting==='append'" :disabled="!operator || !appendText.trim() || Boolean(acting)">追加要求</v-btn></form>
          <form v-if="canContinue && !detailStale" @submit.prevent="submitAction('continue')"><v-textarea v-model="continueText" label="续接已结束任务的新要求" rows="2" auto-grow hide-details="auto" />
            <v-btn type="submit" variant="outlined" :loading="acting==='continue'" :disabled="!operator || !continueText.trim() || Boolean(acting)">续接任务</v-btn></form>
          <div v-if="canAnswer && !detailStale" class="answer"><template v-if="task.question.method==='confirm'"><p class="muted">这是操作授权，不接受文字替代。请核对上方问题原文。</p>
              <v-btn color="primary" :loading="acting==='answer'" :disabled="questionChanged || !operator || Boolean(acting)" @click="submitAction('answer',true)">明确同意</v-btn>
              <v-btn variant="outlined" :disabled="questionChanged || !operator || Boolean(acting)" @click="submitAction('answer',false)">明确拒绝</v-btn></template>
            <template v-else-if="task.question.method==='select'"><v-select v-model="selectedAnswer" :items="task.question.options" label="选择实际回答" hide-details="auto" />
              <v-btn variant="outlined" :loading="acting==='answer'" :disabled="questionChanged || !operator || selectedAnswer===null || Boolean(acting)" @click="submitAction('answer')">提交选择</v-btn></template>
            <template v-else><v-textarea v-model="answerText" label="补充信息（允许空字符串）" rows="2" auto-grow hide-details="auto" />
              <v-btn variant="outlined" :loading="acting==='answer'" :disabled="questionChanged || !operator || Boolean(acting)" @click="submitAction('answer')">提交回答</v-btn></template></div>
          <v-btn v-if="canCancel && !detailStale" variant="outlined" color="error" :loading="acting==='cancel'" :disabled="!operator || Boolean(acting)" @click="submitAction('cancel')">确认取消此任务</v-btn>
          <v-alert v-if="actionError" type="error" variant="tonal" role="alert">{{ actionError }}</v-alert>
          <div v-if="actionResult" class="action-result"><p>后端实际返回：{{ actionResult.action }}。这不代替重新读取当前任务状态。</p>
            <details v-if="developerDetails"><summary>查看操作原始结果</summary><pre>{{ JSON.stringify(actionResult.result,null,2) }}</pre></details></div>
        </section>
        <section class="files"><h3>已登记交付副本</h3><p class="muted">登记只说明副本已保存；是否上传以最近一次平台回执为准。下载只获取已保存副本，不会发往 QQ。</p>
          <p v-if="!detail.files.length" class="muted">当前没有已登记的文件。</p>
          <ul v-else><li v-for="file in detail.files" :key="file.id"><strong>{{ file.name }}</strong> · {{ file.size }} 字节 · 副本已登记
            <p v-if="file.note" class="original-text">{{ file.note }}</p>
            <p v-if="file.upload===null" class="muted">暂无上传记录；不据此推断平台实际状态。</p>
            <template v-else><p>{{ uploadLabel(file.upload.status) }}</p>
              <p class="muted">尝试 / 回执：{{ localTime(file.upload.created) }} / {{ localTime(file.upload.ended) }}</p>
              <p v-if="file.upload.platform_file_id" class="muted">平台文件回执：{{ file.upload.platform_file_id }}</p>
              <p v-if="file.upload.error" class="original-text">错误原文：{{ file.upload.error }}</p>
              <details v-if="developerDetails"><summary>查看最近一次上传回执原文</summary><pre>{{ JSON.stringify(file.upload,null,2) }}</pre></details></template>
            <v-btn variant="outlined" :loading="fileReading===file.id" :disabled="fileReading!==null" @click="downloadFile(file)">下载副本</v-btn></li></ul>
          <v-alert v-if="fileError" type="error" variant="tonal" role="alert">下载失败：{{ fileError }}</v-alert>
          <p v-if="downloadNotice" class="muted" role="status">{{ downloadNotice }}</p></section>
        <section class="events"><h3>任务过程</h3><p class="muted">点击单条读取实际文字与图像结果；完整事件结构只在开发者模式显示，不一次加载全部上下文。</p>
          <v-alert v-if="eventError" type="error" variant="tonal" role="alert">事件读取失败：{{ eventError }}</v-alert>
          <p v-if="!events.length" class="muted">当前没有已保存事件。</p>
          <ol v-else><li v-for="event in events" :key="event.id"><div><strong>{{ eventLabel(event) }}</strong><span class="muted">{{ localTime(event.created) }}<template v-if="event.tool_name"> · {{ event.tool_name }} {{ event.browser_method || '' }}</template></span></div>
            <p v-if="developerDetails" class="original-text">{{ event.preview }}</p><p v-if="developerDetails && event.truncated" class="muted">预览已截短；完整原文须单独读取。</p>
            <v-btn variant="outlined" :loading="eventReading===event.id" :disabled="eventReading!==null" @click="readEvent(event)">{{ fullEvents[event.id]?'重读此条详情':'读取此条详情' }}</v-btn>
            <div v-if="fullEvents[event.id]" class="task-event-content">
              <template v-for="(part,index) in eventContent(fullEvents[event.id].record)" :key="index">
                <pre v-if="part.type==='text'">{{ part.text }}</pre>
                <img v-else :src="`data:${part.mimeType};base64,${part.data}`" alt="此条原生工具结果中的实际图像" loading="lazy" style="max-width:100%;height:auto" />
              </template>
              <p v-if="fullEvents[event.id].record.body.summary">{{ fullEvents[event.id].record.body.summary }}</p>
              <pre v-if="fullEvents[event.id].record.body.error">{{ fullEvents[event.id].record.body.error }}</pre>
              <p v-if="!developerDetails && !eventContent(fullEvents[event.id].record).length" class="muted">此记录没有文字或图像结果；原生结构在设置中的开发者模式查看。</p>
            </div>
            <details v-if="developerDetails && fullEvents[event.id]"><summary>查看此条完整记录 · {{ localTime(fullEvents[event.id].readAt) }} 快照</summary>
              <p class="muted">模型调用的 response 等字段可能随后补写；此处只反映上次读取，必要时点“重读此条原文”。</p>
              <pre>{{ JSON.stringify(fullEvents[event.id].record,null,2) }}</pre></details></li></ol>
          <v-btn v-if="moreEvents" variant="outlined" :loading="detailLoading" :disabled="dirty || detailLoading || refreshing" @click="readDetail(true)">读取更多事件预览</v-btn>
        </section>
        <TaskSkillCandidates v-if="['done','failed','cancelled'].includes(task.status)" :key="`${selectedScene}:${task.id}`" :scene="selectedScene" :task-id="task.id"
          :status="task.status" :container="task.container" :configured="Boolean(state?.configured)" />
      </template>
    </section>
  </div>
</template>

<style scoped>
.host-tasks{max-width:1280px;margin-inline:auto}.page-intro,.section-heading,.task-header{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 450px}.page-intro h1{margin:0 0 10px}.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 14px}.surface h3{font-size:16px;margin:20px 0 8px}.form-grid,.editor-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:12px}.editor-grid{border:0;padding:0;min-width:0}
.form-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0}.scene-facts{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.scene-facts>*{overflow-wrap:anywhere}.network-facts{border-left:3px solid var(--line);padding-left:12px;margin:14px 0;overflow-wrap:anywhere}.network-facts>*{display:block;margin:4px 0}.task-list,.files ul,.events ol{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.task-list li,.files li,.events li,.question,.task-actions,.action-result{border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0;overflow-wrap:anywhere}.task-list li.selected{border-color:var(--primary);background:var(--selected-bg)}.task-list :deep(.v-btn){height:auto;min-height:44px;white-space:normal;text-align:left;max-width:100%}.task-list p{margin:6px 0}
.original-text,.host-tasks pre{white-space:pre-wrap;overflow-wrap:anywhere}.task-header strong{font-size:18px}.task-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:12px}.task-facts dt{font-size:12px;color:var(--muted)}.task-facts dd{margin:4px 0 0}.task-actions>form,.task-actions>.answer{display:grid;gap:10px;margin:14px 0}.answer{grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}.answer>p,.answer>.v-input{grid-column:1/-1}
.events li>div:first-child{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.host-tasks details{margin-top:10px}.host-tasks summary{cursor:pointer;min-height:44px}.host-tasks pre{font-size:13px}.host-tasks :deep(.v-btn){min-height:44px}.host-tasks :deep(.v-alert),.host-tasks .muted{overflow-wrap:anywhere}
.live-preview{border:1px solid var(--line);border-radius:10px;padding:14px;margin:16px 0;min-width:0}.live-preview h3{margin:0}.live-preview pre{max-height:32rem;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.session-download{margin:16px 0}.session-link{display:inline-flex;align-items:center;min-height:44px;padding:8px 14px;border:1px solid var(--line);border-radius:8px;color:var(--primary);font-weight:700;text-decoration:none}.session-link:hover{text-decoration:underline}.session-link:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}.task-list li,.files li,.events li,.question,.task-actions{padding:12px}}
</style>
