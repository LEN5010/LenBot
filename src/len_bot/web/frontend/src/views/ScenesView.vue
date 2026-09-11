<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiArrowLeft, mdiRefresh, mdiMagnify, mdiMessageOutline } from '@mdi/js'
import { api, fmtTime, attentionReason, queryString } from '../api.js'
import { useAppState, loadScopes } from '../composables/useAppState.js'
import PageHeader from '../components/PageHeader.vue'
import EntityLink from '../components/EntityLink.vue'
import StatusBadge from '../components/StatusBadge.vue'
import ResourceViewer from '../components/ResourceViewer.vue'
import MessageItem from '../components/MessageItem.vue'
import SceneInspector from '../components/SceneInspector.vue'
import SceneSettingsForm from '../components/SceneSettingsForm.vue'

const route = useRoute(), router = useRouter(), appState = useAppState(), display = useDisplay()
const scalar = value => typeof value === 'string' ? value : ''
const sceneId = computed(() => scalar(route.params.sceneId)), eventId = computed(() => scalar(route.query.event))
const tabNames = ['messages', 'attention', 'history', 'participants', 'preferences', 'jobs', 'deliveries', 'settings']
const tab = computed(() => tabNames.includes(route.query.tab) ? route.query.tab : 'messages')
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const mobile = computed(() => display.width.value < 1024), wide = computed(() => display.width.value >= 1600)
const sceneSearch = ref(''), participantSearch = ref('')
const groupNumber = ref(''), groupError = ref('')
const scenes = computed(() => appState.scenes.filter(item => `${item.display_name} ${item.scene_id}`.toLowerCase().includes((sceneSearch.value || '').toLowerCase())))
const directoryLoading = ref(false), detail = ref(null), detailLoading = ref(false), detailError = ref(''), detailMissing = ref(false), detailReadAt = ref(null)
const messages = ref([]), messageLoading = ref(false), olderLoading = ref(false), messageError = ref(''), messageLoaded = ref(false), messageReadAt = ref(null), snapshot = ref(null), nextBefore = ref(null), hasMore = ref(false), newPage = ref(null), historical = ref(false), messageScroll = ref(null), messageContent = ref(null)
const selectedEvent = ref(null), relations = ref(null), inspectorLoading = ref(false), inspectorError = ref(''), inspectorReadAt = ref(null)
const aux = ref(null), auxLoading = ref(false), auxError = ref(''), auxReadAt = ref(null)
const retryConfirmation = ref(null), retrySaving = ref(false), retryError = ref(''), feedback = ref(''), retryingBatch = ref(null)
let detailRequest = 0, messageRequest = 0, inspectorRequest = 0, auxRequest = 0
let scrollAnchor = null, pendingPosition = false, contentObserver = null, retryPoll = null
const participants = computed(() => Object.values(detail.value?.session.participants || {}).filter(item => `${item.actor_id} ${item.nickname || ''} ${item.card || ''}`.toLowerCase().includes((participantSearch.value || '').toLowerCase())))
const focused = computed(() => Object.entries(detail.value?.session.focused_participants || {}))
const auxPages = computed(() => Math.max(1, Math.ceil((aux.value?.total || 0) / (aux.value?.page_size || 30))))
const deliveryType = computed(() => ['MESSAGE_SENT', 'MESSAGE_SEND_FAILED', 'ACTION_SHADOWED'].includes(route.query.delivery) ? route.query.delivery : 'MESSAGE_SENT')
const deliveryOptions = [{ title: 'MESSAGE_SENT · 实际发送记录', value: 'MESSAGE_SENT' }, { title: 'MESSAGE_SEND_FAILED · 发送失败', value: 'MESSAGE_SEND_FAILED' }, { title: 'ACTION_SHADOWED · Shadow 记录', value: 'ACTION_SHADOWED' }]
const path = suffix => `/api/cockpit/scenes/${encodeURIComponent(sceneId.value)}${suffix}`
function setTab(value) { router.push({ name: 'scene', params: { sceneId: sceneId.value }, query: { ...route.query, tab: value, page: undefined, before: undefined, snapshot: undefined } }) }
function setPage(value) { router.push({ query: { ...route.query, page: value === 1 ? undefined : String(value) } }) }
function inspect(event) { router.push({ name: 'scene', params: { sceneId: sceneId.value }, query: { ...route.query, event: event.id } }) }
function closeInspector() { router.push({ query: { ...route.query, event: undefined } }) }
function backToScenes() { router.push({ name: 'scenes', query: route.query.query ? { query: route.query.query } : {} }) }
function filterDirectory() { router.replace({ query: { ...route.query, query: sceneSearch.value || undefined } }) }
function configureGroup() {
  const group = groupNumber.value.trim()
  if (!/^[1-9]\d*$/.test(group)) { groupError.value='请填写实际 QQ 群号'; return }
  groupError.value=''
  router.push({name:'scene',params:{sceneId:`group:${group}`},query:{tab:'settings'}})
}
function setDelivery(value) { router.push({ query: { ...route.query, delivery: value, before: undefined, snapshot: undefined } }) }
function olderDeliveries() { router.push({ query: { ...route.query, before: String(aux.value.next_before), snapshot: String(aux.value.snapshot_rowid) } }) }
function latestDeliveries() { router.push({ query: { ...route.query, before: undefined, snapshot: undefined } }) }
async function loadDirectory() { directoryLoading.value = true; try { await loadScopes(true) } finally { directoryLoading.value = false } }
async function loadDetail(reset = false) {
  const id = sceneId.value, own = ++detailRequest
  if (!id) return
  if (reset) { detail.value = null; detailReadAt.value = null }
  detailLoading.value = true; detailError.value = ''; detailMissing.value = false
  try { const data = await api(path('')); if (own === detailRequest) { detail.value = data; detailReadAt.value = data.sampled_at } }
  catch (error) { if (own === detailRequest) { detailError.value = error.message; detailMissing.value = error.status === 404; if (detailMissing.value) detail.value = null } }
  finally { if (own === detailRequest) detailLoading.value = false }
}
function visibleTimeline() {
  return messageScroll.value && messageContent.value && tab.value === 'messages' && (!mobile.value || !eventId.value) && messageScroll.value.getClientRects().length
}
function captureScrollAnchor() {
  if (!visibleTimeline() || pendingPosition) return
  const area = messageScroll.value
  if (!mobile.value && area.scrollHeight - area.clientHeight - area.scrollTop < 4) {
    scrollAnchor = { mode: 'latest', scene: sceneId.value }; return
  }
  const edge = mobile.value ? 0 : area.getBoundingClientRect().top
  const first = [...messageContent.value.querySelectorAll('[data-event-id]')].find(item => item.getBoundingClientRect().bottom > edge)
  if (first) scrollAnchor = { mode: 'event', scene: sceneId.value, id: first.dataset.eventId, offset: first.getBoundingClientRect().top - edge }
}
function restoreScrollAnchor() {
  if (!visibleTimeline() || !scrollAnchor || scrollAnchor.scene !== sceneId.value) return
  const area = messageScroll.value
  if (scrollAnchor.mode === 'latest') {
    if (mobile.value) messageContent.value.lastElementChild?.scrollIntoView({ block: 'end' })
    else area.scrollTop = area.scrollHeight
  } else {
    const target = messageContent.value.querySelector(`[data-event-id="${CSS.escape(scrollAnchor.id)}"]`)
    if (!target) return
    const edge = mobile.value ? 0 : area.getBoundingClientRect().top
    const delta = target.getBoundingClientRect().top - edge - scrollAnchor.offset
    if (mobile.value) window.scrollBy({ top: delta })
    else area.scrollTop += delta
  }
  pendingPosition = false
}
async function positionTimeline(anchor) {
  scrollAnchor = { ...anchor, scene: sceneId.value }; pendingPosition = true
  await nextTick()
  restoreScrollAnchor()
}
function scrollToMessage(id) {
  return positionTimeline({ mode: 'event', id, offset: mobile.value ? 24 : 12 })
}
async function loadMessages({ older = false, refresh = false, locate = '' } = {}) {
  const id = sceneId.value, own = ++messageRequest
  if (!id) return
  if (older) captureScrollAnchor()
  if (older) olderLoading.value = true; else messageLoading.value = true
  messageError.value = ''
  const query = { limit: 50, ...(older ? { before: nextBefore.value, snapshot_rowid: snapshot.value } : {}), ...(locate ? { event_id: locate } : {}) }
  try {
    const result = await api(`${path('/messages')}?${queryString(query)}`)
    if (own !== messageRequest) return
    messageReadAt.value = Date.now() / 1000
    if (refresh && messageLoaded.value) {
      if ((result.items[0]?.rowid || 0) > (messages.value.at(-1)?.rowid || 0)) newPage.value = result
      return
    }
    if (older) {
      const existing = new Set(messages.value.map(item => item.id))
      messages.value = [...result.items.filter(item => !existing.has(item.id)).reverse(), ...messages.value]
      historical.value = true
    } else {
      scrollAnchor = { mode: locate ? 'event' : 'latest', id: locate, offset: mobile.value ? 24 : 12, scene: id }; pendingPosition = true
      messages.value = [...result.items].reverse(); snapshot.value = result.snapshot_rowid; newPage.value = null; historical.value = Boolean(locate)
    }
    nextBefore.value = result.next_before; hasMore.value = result.has_more; messageLoaded.value = true
    await nextTick()
    restoreScrollAnchor()
  } catch (error) { if (own === messageRequest) messageError.value = error.message }
  finally { if (own === messageRequest) { messageLoading.value = false; olderLoading.value = false } }
}
async function viewLatest() {
  ++messageRequest
  if (!newPage.value) { await loadMessages(); return }
  const result = newPage.value
  messages.value = [...result.items].reverse(); snapshot.value = result.snapshot_rowid; nextBefore.value = result.next_before; hasMore.value = result.has_more; newPage.value = null; historical.value = false; messageLoading.value = false; olderLoading.value = false
  await positionTimeline({ mode: 'latest' })
}
async function loadInspector({ locate = true } = {}) {
  const id = eventId.value, scene = sceneId.value, own = ++inspectorRequest
  if (!id || !scene) { selectedEvent.value = null; relations.value = null; inspectorLoading.value = false; return }
  inspectorLoading.value = true; inspectorError.value = ''; selectedEvent.value = null; relations.value = null; inspectorReadAt.value = null
  try {
    const [eventResult, relationResult] = await Promise.allSettled([
      api(`/api/cockpit/events/${encodeURIComponent(id)}?${queryString({ scene_id: scene })}`),
      api(`/api/cockpit/relations?${queryString({ scene_id: scene, event_id: id })}`),
    ])
    if (own !== inspectorRequest) return
    if (eventResult.status === 'rejected') throw eventResult.reason
    selectedEvent.value = eventResult.value; inspectorReadAt.value = Date.now() / 1000
    if (relationResult.status === 'fulfilled') relations.value = relationResult.value
    else inspectorError.value = `关联读取失败：${relationResult.reason.message}`
    if (locate && tab.value === 'messages' && ['GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED', 'MESSAGE_SENT'].includes(eventResult.value.event_type)) {
      if (!messages.value.some(item => item.id === id)) await loadMessages({ locate: id })
      else await scrollToMessage(id)
    }
  } catch (error) { if (own === inspectorRequest) inspectorError.value = error.status === 404 ? '事件不存在或不属于当前场景。' : error.message }
  finally { if (own === inspectorRequest) inspectorLoading.value = false }
}
async function loadAux(reset = false) {
  const id = sceneId.value, selectedTab = tab.value, own = ++auxRequest
  if (reset) { aux.value = null; auxReadAt.value = null }
  auxError.value = ''; auxLoading.value = false
  if (!id || !['attention', 'history', 'jobs', 'deliveries'].includes(selectedTab)) return
  auxLoading.value = true
  let url
  const query = queryString({ scene_id: id, page: page.value, page_size: 30 })
  if (selectedTab === 'attention') url = `${path('/pending-wakes')}?${queryString({ page: page.value, page_size: 30 })}`
  if (selectedTab === 'history') url = `/api/cockpit/history-batches?${query}`
  if (selectedTab === 'jobs') url = `/api/cockpit/jobs?${query}`
  if (selectedTab === 'deliveries') url = `/api/cockpit/events?${queryString({ scene_id: id, event_type: deliveryType.value, limit: 50, before: scalar(route.query.before), snapshot_rowid: scalar(route.query.snapshot) })}`
  try { const result = await api(url); if (own === auxRequest) { aux.value = result; auxReadAt.value = Date.now() / 1000 } }
  catch (error) { if (own === auxRequest) auxError.value = error.message }
  finally { if (own === auxRequest) auxLoading.value = false }
}
function askRetry(batch) { retryConfirmation.value = { id: batch.id, scene: sceneId.value, range: `${batch.start_rowid}:${batch.start_offset} → ${batch.end_rowid}:${batch.end_offset}`, error: batch.failure_detail || batch.error_type }; retryError.value = '' }
function stopRetryPoll() { if (retryPoll) { clearInterval(retryPoll); retryPoll = null } retryingBatch.value = null }
async function pollRetry(id, scene) {
  try {
    const batch = await api(`/api/cockpit/history-batches/${encodeURIComponent(id)}?${queryString({ scene_id: scene })}`)
    if (sceneId.value !== scene || retryingBatch.value !== id) return
    if (batch.status === 'pending') return
    stopRetryPoll(); await loadDetail(); await loadAux()
    if (batch.status === 'completed') feedback.value = '此区间已完成历史摘要覆盖。'
    else retryError.value = batch.failure_detail || batch.error_type || '此区间处理失败，请查看失败详情后再重试。'
  } catch (error) {
    if (sceneId.value === scene && retryingBatch.value === id) retryError.value = error.message
  }
}
async function retryHistory() {
  if (!retryConfirmation.value || retrySaving.value) return
  const pending = { ...retryConfirmation.value }; retrySaving.value = true; retryError.value = ''
  try {
    const result = await api(`/api/cockpit/history-batches/${encodeURIComponent(pending.id)}/retry`, { method: 'POST', body: JSON.stringify({ scene_id: pending.scene }) })
    if (sceneId.value !== pending.scene) return
    feedback.value = result.message || '已提交此区间的维护重试，正在等待处理结果。'; retryConfirmation.value = null; retryingBatch.value = pending.id
    retryPoll = setInterval(() => pollRetry(pending.id, pending.scene), 1500); await pollRetry(pending.id, pending.scene)
    await Promise.all([loadDetail(), loadAux()])
  } catch (error) { if (sceneId.value === pending.scene) retryError.value = error.message }
  finally { retrySaving.value = false }
}
async function refresh() {
  const requests = [loadDirectory()]
  if (sceneId.value) { requests.push(loadDetail()); if (tab.value === 'messages') requests.push(loadMessages({ refresh: true })); else requests.push(loadAux()); if (eventId.value) requests.push(loadInspector({ locate: false })) }
  await Promise.all(requests)
}
function onVisible() { if (document.visibilityState === 'visible') refresh() }
onMounted(() => { loadDirectory(); document.addEventListener('visibilitychange', onVisible); window.addEventListener('scroll', captureScrollAnchor, { passive: true }) })
onBeforeUnmount(() => { ++detailRequest; ++messageRequest; ++inspectorRequest; ++auxRequest; stopRetryPoll(); contentObserver?.disconnect(); document.removeEventListener('visibilitychange', onVisible); window.removeEventListener('scroll', captureScrollAnchor) })
watch(messageContent, async content => {
  contentObserver?.disconnect()
  if (!content) return
  contentObserver = new ResizeObserver(restoreScrollAnchor)
  contentObserver.observe(content)
  await nextTick()
  restoreScrollAnchor()
}, { flush: 'post' })
watch(() => route.query.query, value => { sceneSearch.value = scalar(value) }, { immediate: true })
watch(sceneId, value => {
  stopRetryPoll()
  ++detailRequest; ++messageRequest; ++inspectorRequest
  scrollAnchor = null; pendingPosition = false
  detailLoading.value = false; messageLoading.value = false; olderLoading.value = false; inspectorLoading.value = false
  detail.value = null; detailError.value = ''; detailReadAt.value = null; messages.value = []; messageLoaded.value = false; messageError.value = ''; newPage.value = null; snapshot.value = null; nextBefore.value = null; hasMore.value = false; historical.value = false; selectedEvent.value = null; relations.value = null; inspectorError.value = ''; retryConfirmation.value = null; retryError.value = ''; feedback.value = ''; participantSearch.value = ''
  if (value) { loadDetail(true); loadMessages(); loadInspector() }
}, { immediate: true })
watch(() => [sceneId.value, tab.value, page.value, deliveryType.value, route.query.before, route.query.snapshot], () => loadAux(true), { immediate: true })
watch(eventId, async (value, previous) => {
  if (value === previous) return
  loadInspector()
  if (!value && previous) await scrollToMessage(previous)
})
</script>

<template>
  <section class="scenes-page">
    <PageHeader title="场景消息" description="从原话查看注意力、已有工作与真实回执。"><v-btn :prepend-icon="mdiRefresh" variant="outlined" :loading="directoryLoading || detailLoading || messageLoading || auxLoading" @click="refresh">刷新</v-btn></PageHeader>
    <v-alert v-if="appState.sceneError" type="error" variant="tonal" class="mb-4">场景目录读取失败：{{ appState.sceneError }}</v-alert>
    <div class="scene-workspace" :class="{ 'has-scene': sceneId, 'has-inspector': eventId, 'wide-workspace': wide }">
      <v-card v-show="!mobile || !sceneId" class="scene-directory">
        <v-card-text class="directory-search"><v-form @submit.prevent="filterDirectory"><v-text-field v-model="sceneSearch" :prepend-inner-icon="mdiMagnify" label="查找场景" hide-details clearable @click:clear="filterDirectory" /></v-form><v-expansion-panels class="mt-3"><v-expansion-panel title="配置另一个群"><v-expansion-panel-text><v-form @submit.prevent="configureGroup"><v-text-field v-model="groupNumber" label="QQ 群号" inputmode="numeric" :error-messages="groupError" /><v-btn type="submit" color="primary" variant="tonal">打开本群设置</v-btn></v-form></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></v-card-text>
        <v-list class="scene-list" lines="three" aria-label="场景目录">
          <v-list-item v-for="scene in scenes" :key="scene.scene_id" :to="{ name: 'scene', params: { sceneId: scene.scene_id }, query: { ...(route.query.query ? {query:route.query.query} : {}), ...(!scene.has_history ? {tab:'settings'} : {}) } }" :active="scene.scene_id === sceneId" color="primary" class="scene-list-row">
            <div class="scene-list-content"><strong>{{ scene.display_name }}</strong><span class="scene-id">{{ scene.scene_id }}</span><time>{{ scene.has_history?'最近活动 '+fmtTime(scene.last_event_at):'已配置，尚无原话记录' }}</time><span v-if="scene.scene_type==='group'" class="scene-id">{{ !scene.settings?'未配置':!scene.settings.enabled?'已停用':scene.settings.chat?'普通聊天已开放':'仅白名单聊天／命令与公告' }}</span><div class="scene-counts"><span>待处理 {{ scene.pending_wake_count }}</span><span>工作 {{ scene.active_job_count }}</span></div></div>
          </v-list-item>
        </v-list>
        <p v-if="appState.loadedScenes && !scenes.length" class="empty-copy">{{ sceneSearch ? '没有符合搜索的场景。' : '还没有场景记录。' }}</p>
        <p class="directory-count">{{ scenes.length }} 个场景</p>
      </v-card>
      <v-card v-if="!sceneId && !mobile" class="scene-placeholder"><v-icon :icon="mdiMessageOutline" size="40" /><h2>选择一个场景</h2><p>原话、参与者和关联记录会显示在这里。</p></v-card>
      <div v-if="sceneId" v-show="!mobile || !eventId" class="scene-main">
        <v-btn v-if="mobile" variant="text" :prepend-icon="mdiArrowLeft" class="mb-3" @click="backToScenes">返回场景列表</v-btn>
        <v-alert v-if="detailError&&tab!=='settings'" :type="detailMissing ? 'warning' : 'error'" variant="tonal" class="mb-4">{{ detailError }}<p v-if="detailReadAt">保留上次读取：{{ fmtTime(detailReadAt) }}</p></v-alert>
        <v-skeleton-loader v-if="detailLoading && !detail" type="list-item-two-line" />
        <template v-if="detail||tab==='settings'||detailMissing">
          <v-card class="scene-heading"><v-card-text><h2>{{ detail?.session.display_name||sceneId }}</h2><div class="scene-heading-meta"><EntityLink type="scene" :id="sceneId" :scene-id="sceneId" :label="sceneId" /><span v-if="detail">{{ Object.keys(detail.session.participants).length }} 位已记录成员</span><span v-if="detailReadAt">读取于 {{ fmtTime(detailReadAt) }}</span></div></v-card-text><v-tabs :model-value="tab" color="primary" show-arrows @update:model-value="setTab"><v-tab value="messages">原话</v-tab><v-tab value="attention">注意力</v-tab><v-tab value="history">摘要覆盖</v-tab><v-tab value="participants">参与者</v-tab><v-tab value="preferences">互动偏好</v-tab><v-tab value="jobs">工作</v-tab><v-tab value="deliveries">发送记录</v-tab><v-tab v-if="sceneId.startsWith('group:')" value="settings">本群设置</v-tab></v-tabs></v-card>
          <v-alert v-if="feedback" type="success" variant="tonal" class="mt-4" role="status">{{ feedback }}</v-alert>
          <v-card v-if="tab==='settings'" class="scene-tab-card"><v-card-text><SceneSettingsForm :scene-id="sceneId" @saved="loadDirectory" /></v-card-text></v-card>
          <v-card v-else-if="!detail" class="scene-tab-card"><v-card-text><p class="muted-copy">此场景尚无可读取的原话状态，配置群规则不需要先制造聊天记录。</p><v-btn v-if="sceneId.startsWith('group:')" color="primary" variant="tonal" class="mt-4" @click="setTab('settings')">打开本群设置</v-btn></v-card-text></v-card>
          <v-card v-else-if="tab === 'messages'" class="scene-tab-card">
            <div class="timeline-toolbar"><span>原话与实际发送记录</span><v-btn v-if="historical || newPage" size="small" color="primary" variant="tonal" :disabled="messageLoading || olderLoading" @click="viewLatest">{{ newPage ? '有新消息 · 查看最新' : '返回最新消息' }}</v-btn></div>
            <v-alert v-if="messageError" type="error" variant="tonal" class="mx-4 mb-3">{{ messageError }}<div v-if="messageReadAt">上次读取于 {{ fmtTime(messageReadAt) }}</div></v-alert>
            <v-progress-linear v-if="messageLoading" indeterminate aria-label="正在读取场景原话" />
            <div ref="messageScroll" class="message-scroll" tabindex="0" aria-label="场景原话时间线" @scroll.passive="captureScrollAnchor">
              <div ref="messageContent" class="message-content">
                <div v-if="hasMore" class="older-messages"><v-btn variant="outlined" :loading="olderLoading" :disabled="olderLoading || messageLoading" @click="loadMessages({ older: true })">读取更早原话</v-btn></div>
                <p v-else-if="messageLoaded && messages.length" class="history-start">已到此场景保存的最早原话</p>
                <MessageItem v-for="event in messages" :key="event.id" :event="event" :selected="event.id === eventId" @inspect="inspect" />
                <p v-if="messageLoaded && !messages.length" class="empty-copy">此场景尚无原话记录。</p>
              </div>
            </div>
          </v-card>
          <v-card v-else class="scene-tab-card">
            <v-card-text class="scene-detail-body">
              <v-progress-linear v-if="auxLoading" indeterminate aria-label="正在读取场景详情" />
              <v-alert v-if="auxError" type="error" variant="tonal" class="mb-4">{{ auxError }}<div v-if="auxReadAt">上次读取于 {{ fmtTime(auxReadAt) }}</div></v-alert>
              <template v-if="tab === 'attention'">
                <h3>注意力扫描与待处理来源</h3><p class="muted-copy">扫描到原始位置 {{ detail.session.attention_scanned_event_rowid }}；当前已观察到 {{ detail.session.last_observed_event_rowid }}。扫描位置与模型实际读取分别记录。</p>
                <p>待处理来源 {{ aux?.total ?? detail.session.pending_wake_count }} 项</p><article v-for="wake in aux?.items || []" :key="wake.event_id" class="detail-record"><div class="record-meta"><v-chip variant="tonal" size="small">{{ wake.certain ? '确定唤醒来源' : '旁听机会' }}</v-chip><span>{{ wake.actor_id || '运行事件' }}</span><span>位置 {{ wake.rowid }}</span></div><p>{{ wake.reasons.map(attentionReason).join(' · ') || '未记录原因' }}</p><EntityLink type="event" :id="wake.event_id" :scene-id="sceneId" label="查看来源原话与关联" /></article><p v-if="aux && !aux.items.length" class="empty-copy">没有尚待处理的唤醒来源。</p>
                <h3>真实送达建立的连续关注</h3><div v-for="[actor, until] in focused" :key="actor" class="detail-record"><strong class="breakable">{{ actor }}</strong><p>截止 {{ fmtTime(until) }}</p></div><p v-if="!focused.length" class="muted-copy">暂无连续关注记录。</p>
                <v-expansion-panels variant="accordion"><v-expansion-panel title="事实状态与版本"><v-expansion-panel-text><p>事实版本 {{ detail.session.version }} · 认识版本 {{ detail.session.knowledge_revision }}</p><p>最近实际发言 {{ fmtTime(detail.session.last_bot_message_at) }}</p><p>发言后新增群友消息 {{ detail.session.human_messages_since_bot }}</p><EntityLink v-if="detail.session.last_bot_message_event_id" type="event" :id="detail.session.last_bot_message_event_id" :scene-id="sceneId" label="最近实际发言原始回执" /></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
              </template>
              <template v-else-if="tab === 'history'">
                <h3>历史摘要覆盖</h3><v-alert :type="detail.maintenance?.ready ? 'info' : 'warning'" variant="tonal" class="my-4">{{ detail.maintenance?.reason }}</v-alert><p class="muted-copy">摘要按原始范围保存，未成功的区间保留原文。摘要提供定位，工作和认识仍须实际读取证据。</p><p v-if="detail.history_status.initial_history_boundary" class="muted-copy">初始历史边界 {{ detail.history_status.initial_history_boundary }}，边界之前的原文未据此标为已总结。</p><p>尚未成功覆盖 {{ detail.history_status.unsuccessful_count }} 个批次</p>
                <article v-for="batch in aux?.items || []" :key="batch.id" class="detail-record"><div class="record-meta"><StatusBadge domain="summary" :status="batch.status" /><strong>{{ batch.start_rowid }}:{{ batch.start_offset }} → {{ batch.end_rowid }}:{{ batch.end_offset }}</strong><span>版本 {{ batch.generation_version }}</span><time>{{ fmtTime(batch.completed_at || batch.created_at) }}</time></div><p v-if="batch.error_type" class="error-copy">{{ batch.failure_detail || batch.error_type }} · 此区间尚未成功覆盖</p><p class="two-lines">{{ batch.summary || '尚无摘要正文。' }}</p><v-btn v-if="['failed', 'pending'].includes(batch.status)" variant="outlined" :disabled="!detail.maintenance?.ready || retrySaving" @click="askRetry(batch)">重试此区间</v-btn><v-expansion-panels variant="accordion" class="mt-3"><v-expansion-panel title="摘要全文与原文定位"><v-expansion-panel-text><ResourceViewer title="完整摘要" :content="batch.summary" /><h4>来源原话</h4><div class="detail-links"><EntityLink v-for="id in batch.source_event_ids" :key="id" type="event" :id="id" :scene-id="sceneId" /></div><h4>关键原话</h4><div class="detail-links"><EntityLink v-for="id in batch.key_event_ids" :key="id" type="event" :id="id" :scene-id="sceneId" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></article><p v-if="aux && !aux.items.length" class="empty-copy">尚未生成历史摘要，原文保留。</p>
              </template>
              <template v-else-if="tab === 'participants'">
                <h3>参与者</h3><p class="muted-copy">账号昵称与群名片来自已保存的消息；称呼偏好单独保留在认识中。</p><v-text-field v-model="participantSearch" label="查找账号、昵称或群名片" clearable hide-details class="my-4" /><article v-for="person in participants" :key="person.actor_id" class="detail-record"><strong class="breakable">{{ person.card || person.nickname || person.actor_id }}</strong><dl class="participant-facts"><dt>账号</dt><dd>{{ person.actor_id }}</dd><dt>昵称</dt><dd>{{ person.nickname || '未记录' }}</dd><dt>群名片</dt><dd>{{ person.card || '未记录' }}</dd><dt>群角色</dt><dd>{{ person.role || '未记录' }}</dd></dl></article><p v-if="!participants.length" class="empty-copy">没有符合条件的参与者事实。</p>
              </template>
              <template v-else-if="tab === 'preferences'">
                <h3>称呼与互动偏好</h3><article v-for="memory in detail.preferences" :key="memory.id" class="detail-record"><div class="record-meta"><StatusBadge domain="basis" :status="memory.basis" /><span class="breakable">{{ memory.subject }}</span></div><p class="two-lines">{{ memory.statement }}</p><div class="record-meta"><EntityLink type="memory" :id="memory.id" :scene-id="sceneId" label="认识全文与修订链" /><span>到期 {{ memory.expires_at ? fmtTime(memory.expires_at) : '未设置' }}</span></div><v-expansion-panels variant="accordion" class="mt-3"><v-expansion-panel title="来源原话"><v-expansion-panel-text><div class="detail-links"><EntityLink v-for="id in memory.evidence" :key="id" type="event" :id="id" :scene-id="sceneId" /></div></v-expansion-panel-text></v-expansion-panel></v-expansion-panels></article><p v-if="!detail.preferences.length" class="empty-copy">暂无有效的称呼与互动偏好。</p>
              </template>
              <template v-else-if="tab === 'jobs'">
                <h3>此场景的信息工作</h3><article v-for="job in aux?.items || []" :key="job.id" class="detail-record"><RouterLink class="two-lines job-title" :to="{ name: 'job', params: { jobId: job.id }, query: { scene: sceneId } }">{{ job.goal }}</RouterLink><div class="record-meta mt-3"><StatusBadge domain="job_execution" :status="job.execution_status" /><StatusBadge domain="job_delivery" :status="job.status" /><span>目标版本 {{ job.revision }}</span><time>{{ fmtTime(job.updated_at) }}</time></div></article><p v-if="aux && !aux.items.length" class="empty-copy">暂无信息工作。</p>
              </template>
              <template v-else-if="tab === 'deliveries'">
                <h3>发送记录</h3><p class="muted-copy">按原始事件类型读取。模拟与 Shadow 会单独标记，送达状态以保存的回执为准。</p><v-select :model-value="deliveryType" :items="deliveryOptions" label="原始事件类型" hide-details class="my-4" @update:model-value="setDelivery" /><v-btn v-if="route.query.before" variant="text" @click="latestDeliveries">返回最新记录</v-btn><MessageItem v-for="event in aux?.items || []" :key="event.id" :event="event" :selected="event.id === eventId" @inspect="inspect" /><p v-if="aux && !aux.items.length" class="empty-copy">此范围没有该类型的发送记录。</p><v-btn v-if="aux?.has_more" variant="outlined" class="mt-4" :disabled="auxLoading" @click="olderDeliveries">读取更早记录</v-btn>
              </template>
              <div v-if="aux && ['attention', 'history', 'jobs'].includes(tab)" class="aux-pagination"><span>共 {{ aux.total }} 项 · 第 {{ aux.page }} 页</span><v-pagination v-if="auxPages > 1" :model-value="page" :length="auxPages" :total-visible="3" @update:model-value="setPage" /></div>
            </v-card-text>
          </v-card>
        </template>
      </div>
      <v-card v-if="sceneId && eventId && (wide || mobile)" class="inline-inspector"><SceneInspector :active="Boolean(eventId)" :event="selectedEvent" :relations="relations" :loading="inspectorLoading" :error="inspectorError" :read-at="inspectorReadAt" @close="closeInspector" @retry="loadInspector" /></v-card>
    </div>
    <v-navigation-drawer v-if="sceneId && !mobile && !wide" :model-value="Boolean(eventId)" temporary location="right" width="420" aria-label="消息关联检查抽屉" @update:model-value="value => { if (!value && eventId) closeInspector() }"><SceneInspector modal :active="Boolean(eventId)" :event="selectedEvent" :relations="relations" :loading="inspectorLoading" :error="inspectorError" :read-at="inspectorReadAt" @close="closeInspector" @retry="loadInspector" /></v-navigation-drawer>
    <v-dialog :model-value="Boolean(retryConfirmation)" :persistent="retrySaving" max-width="580" @update:model-value="value => { if (!value && !retrySaving) retryConfirmation = null }"><v-card><v-card-title class="dialog-title">重试此历史区间</v-card-title><v-card-text v-if="retryConfirmation"><p>场景 {{ retryConfirmation.scene }}</p><p>原始范围 {{ retryConfirmation.range }}</p><p v-if="retryConfirmation.error">原失败记录：{{ retryConfirmation.error }}</p><p>通过已有维护入口提交一次重试，失败证据和原文仍可回查。</p><v-alert v-if="retryError" type="error" variant="tonal" class="mt-4">{{ retryError }}</v-alert></v-card-text><v-card-actions class="dialog-actions"><v-btn variant="text" :disabled="retrySaving" @click="retryConfirmation = null">返回</v-btn><v-btn color="primary" :loading="retrySaving" :disabled="retrySaving" @click="retryHistory">确认重试</v-btn></v-card-actions></v-card></v-dialog>
  </section>
</template>

<style scoped>
.scenes-page{min-width:0}.scenes-page>.page-header{margin-bottom:24px}.scene-workspace{display:grid;grid-template-columns:248px minmax(0,1fr);gap:18px;align-items:start;min-width:0}.scene-workspace.wide-workspace.has-scene.has-inspector{grid-template-columns:240px minmax(0,1fr) 340px}.scene-directory,.scene-main,.scene-tab-card,.scene-heading,.inline-inspector{min-width:0}.scene-directory{overflow:hidden}.directory-search{padding:16px}.scene-list{padding:0 8px}.scene-list-row{margin-bottom:4px;border-radius:8px;min-width:0}.scene-list-row :deep(.v-list-item__content){min-width:0}.scene-list-content{display:flex;flex-direction:column;gap:6px;min-width:0;padding:8px 0}.scene-list-content strong{font-size:14px;line-height:1.6;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.scene-id{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.scene-list-content time,.scene-counts,.directory-count{font-size:11px;color:var(--muted)}.scene-counts{display:flex;gap:14px;flex-wrap:wrap}.directory-count{padding:12px 20px}.scene-placeholder{min-height:360px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;color:var(--muted);padding:24px;text-align:center}.scene-placeholder h2{font-size:18px;color:var(--ink)}.scene-heading h2{font-size:20px;line-height:1.6;overflow-wrap:anywhere}.scene-heading-meta{display:flex;gap:8px 16px;flex-wrap:wrap;margin-top:8px;align-items:center;font-size:12px;color:var(--muted);min-width:0}.scene-heading-meta>*{min-width:0}.scene-tab-card{margin-top:16px}.timeline-toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:12px 16px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}.message-content{min-width:0;display:flow-root}.message-scroll{overflow-anchor:none;padding:4px 16px 12px;max-height:70vh;min-height:360px;overflow:auto;position:relative;scrollbar-gutter:stable}.message-scroll:focus-visible{outline:2px solid rgb(var(--v-theme-primary));outline-offset:-2px}.older-messages{text-align:center;padding:16px 0}.history-start{text-align:center;color:var(--muted);font-size:11px;padding:14px 0}.scene-detail-body{min-width:0;line-height:1.7}.scene-detail-body h3{font-size:17px;margin:24px 0 10px;line-height:1.6}.scene-detail-body h3:first-child{margin-top:0}.scene-detail-body h4{font-size:14px;margin:16px 0 10px}.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}.detail-record{padding:18px 0;border-bottom:1px solid var(--line);min-width:0}.detail-record:last-child{border-bottom:0}.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}.record-meta>*{min-width:0}.record-meta strong{color:var(--ink);overflow-wrap:anywhere}.detail-links{display:grid;gap:10px;min-width:0}.two-lines{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;white-space:pre-wrap}.breakable{overflow-wrap:anywhere}.job-title{font-weight:600;font-size:15px;line-height:1.7}.error-copy{color:rgb(var(--v-theme-error));font-size:13px}.participant-facts{display:grid;grid-template-columns:64px minmax(0,1fr);gap:6px 12px;font-size:13px;margin-top:12px}.participant-facts dt{color:var(--muted)}.participant-facts dd{margin:0;overflow-wrap:anywhere}.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}.aux-pagination{margin-top:20px;color:var(--muted);font-size:12px}.inline-inspector{max-height:calc(100vh - 180px);overflow:hidden;position:sticky;top:20px}.inline-inspector :deep(.scene-inspector){max-height:calc(100vh - 180px)}.dialog-title{white-space:normal}.dialog-actions{padding:16px;flex-wrap:wrap}@media(max-width:1200px){.scene-workspace{grid-template-columns:220px minmax(0,1fr);gap:14px}.scene-heading h2{font-size:18px}}@media(max-width:1023px){.scene-workspace,.scene-workspace.wide-workspace.has-scene.has-inspector{grid-template-columns:minmax(0,1fr)}.scene-list-row{min-height:unset}.scene-list-content{padding:10px 0}.scene-heading h2{font-size:18px}.scene-heading-meta{align-items:flex-start;flex-direction:column}.message-scroll{max-height:none;min-height:240px;overflow:visible;scrollbar-gutter:auto;padding:4px 12px 12px}.inline-inspector{max-height:none;position:static}.inline-inspector :deep(.scene-inspector){max-height:none}.inline-inspector :deep(.inspector-body){overflow:visible}.scene-detail-body{padding:16px}.scene-counts{gap:18px}.directory-search{padding:16px}.timeline-toolbar{padding:12px;align-items:flex-start}.timeline-toolbar>.v-btn{max-width:100%}}
</style>
