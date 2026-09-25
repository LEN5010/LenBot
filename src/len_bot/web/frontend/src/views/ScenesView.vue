<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { mdiArrowLeft, mdiRefresh, mdiMagnify, mdiMessageOutline, mdiChevronDown } from '@mdi/js'
import { api, fmtTime, queryString } from '../api.js'
import { useAppState, loadScopes } from '../composables/useAppState.js'
import { useAuth } from '../composables/useAuth.js'
import { rememberSceneVisit, sceneVisit } from '../composables/sceneVisits.js'
import { sourcePath, withReturn } from '../router/navigation.js'
import PageHeader from '../components/PageHeader.vue'
import EntityLink from '../components/EntityLink.vue'
import StatusBadge from '../components/StatusBadge.vue'
import SceneInspector from '../components/SceneInspector.vue'
import SceneSettingsForm from '../components/SceneSettingsForm.vue'
import RetryHistoryDialog from '../components/scenes/RetryHistoryDialog.vue'
import SceneAttentionTab from '../components/scenes/SceneAttentionTab.vue'
import SceneDeliveriesTab from '../components/scenes/SceneDeliveriesTab.vue'
import SceneHistoryTab from '../components/scenes/SceneHistoryTab.vue'
import SceneJobsTab from '../components/scenes/SceneJobsTab.vue'
import SceneMemoryTab from '../components/scenes/SceneMemoryTab.vue'
import SceneMessagesTab from '../components/scenes/SceneMessagesTab.vue'
import SceneParticipantsTab from '../components/scenes/SceneParticipantsTab.vue'
import ScenePreferencesTab from '../components/scenes/ScenePreferencesTab.vue'
import { useHistoryRetry } from '../components/scenes/useHistoryRetry.js'

const route = useRoute(), router = useRouter(), appState = useAppState(), display = useDisplay()
const scalar = value => typeof value === 'string' ? value : ''
const sceneId = computed(() => scalar(route.params.sceneId)),
  eventId = computed(() => scalar(route.query.event))
const primaryTabs = [
  { value: 'messages', label: '对话' },
  { value: 'memory', label: '本群记忆' },
  { value: 'jobs', label: '工作' },
  { value: 'settings', label: '设置' }
]
const diagnosticTabs = [
  { value: 'attention', label: '注意力与待处理' },
  { value: 'history', label: '摘要覆盖' },
  { value: 'participants', label: '参与者' },
  { value: 'preferences', label: '称呼与互动偏好' },
  { value: 'deliveries', label: '发送记录' }
]
const tabNames = [...primaryTabs, ...diagnosticTabs].map(item => item.value)
const tab = computed(() => tabNames.includes(route.query.tab) ? route.query.tab : 'messages')
const selectedDiagnostic = computed(() => diagnosticTabs.find(item => item.value === tab.value))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const mobile = computed(() => display.width.value < 1024),
  wide = computed(() => display.width.value >= 1600)
const sceneSearch = ref(''), participantSearch = ref('')
const pageArea = ref(null),
  expandedRecords = ref({}),
  settingsReady = ref(false),
  returnNotice = ref('')
const groupNumber = ref(''), groupError = ref('')
const directoryType = computed(() => ['group', 'private'].includes(route.query.type) ? route.query.type : 'all')
const permission = computed(() => ['chat', 'listen', 'broadcast', 'disabled', 'work', 'unconfigured'].includes(route.query.permission) ? route.query.permission : 'all')
function chatMode(item) {
  if (!item?.settings) return 'unconfigured'
  if (!item.settings.enabled) return 'disabled'
  return item.settings.chat ? 'on' : item.settings.listen ? 'listen' : 'off'
}
function workEnabled(item) {
  return Boolean(item.settings?.enabled) && ['workspace', 'python_workspace'].some(id => item.settings.plugins?.[id]?.enabled)
}
function matchesPermission(item, value) {
  if (value === 'all') return true
  if (item.scene_type !== 'group') return false
  if (value === 'work') return workEnabled(item)
  return chatMode(item) === ({ chat: 'on', broadcast: 'off' }[value] || value)
}
const typedScenes = computed(() => appState.scenes.filter(item => directoryType.value === 'all' || item.scene_type === directoryType.value))
const permissionOptions = computed(() => [
  ['all', '全部保存状态'], ['chat', '聊天'], ['listen', '只跟读'], ['broadcast', '仅播报'],
  ['disabled', '已停用'], ['work', '工作插件已开启'], ['unconfigured', '未配置'],
].map(([value, label]) => ({
  value,
  title: `${label}（${typedScenes.value.filter(item => matchesPermission(item, value)).length}）`
})))
const scenes = computed(() => typedScenes.value.filter(item => matchesPermission(item, permission.value) && `${item.display_name} ${item.scene_id}`.toLowerCase().includes((sceneSearch.value || '').trim().toLowerCase())))
const currentScene = computed(() => appState.scenes.find(item => item.scene_id === sceneId.value))
const directoryQuery = computed(() => ({
  query: scalar(route.query.query) || undefined,
  permission: permission.value === 'all' ? undefined : permission.value,
  type: directoryType.value === 'all' ? undefined : directoryType.value,
  return_to: route.query.return_to
}))
const directoryLoading = ref(false),
  detail = ref(null),
  detailLoading = ref(false),
  detailError = ref(''),
  detailMissing = ref(false),
  detailReadAt = ref(null)
const messages = ref([]),
  messageLoading = ref(false),
  olderLoading = ref(false),
  messageError = ref(''),
  messageLoaded = ref(false),
  messageReadAt = ref(null),
  snapshot = ref(null),
  nextBefore = ref(null),
  hasMore = ref(false),
  newPage = ref(null),
  historical = ref(false),
  messageScroll = ref(null),
  messageContent = ref(null)
const selectedEvent = ref(null),
  relations = ref(null),
  inspectorLoading = ref(false),
  inspectorError = ref(''),
  inspectorReadAt = ref(null)
const aux = ref(null), auxLoading = ref(false), auxError = ref(''), auxReadAt = ref(null)
let detailRequest = 0, messageRequest = 0, inspectorRequest = 0, auxRequest = 0
let scrollAnchor = null, pendingPosition = false, contentObserver = null
let sectionPosition = null
let activeScene = '', activeTab = '', activeEvent = '', viewRequest = 0
const auxPages = computed(() => Math.max(1, Math.ceil((aux.value?.total || 0) / (aux.value?.page_size || 30))))
const deliveryType = computed(() => ['MESSAGE_SENT', 'MESSAGE_SEND_FAILED', 'ACTION_SHADOWED'].includes(route.query.delivery) ? route.query.delivery : 'MESSAGE_SENT')
// The history retry state is created here so it follows this page's route
// changes and unmount; tab components render page state and own no requests.
const retryState = useHistoryRetry({sceneId, tab, loadDetail, loadAux})
const { retryConfirmation, retryError, feedback, stopRetryPoll, cancelRetry, disposeRetry } = retryState
const shared = {route, sceneId, eventId, detail, aux, auxLoading, auxError, expandedRecords, participantSearch,
  deliveryType, messages, messageLoading, olderLoading, messageError, messageLoaded, messageReadAt, hasMore, newPage,
  historical, messageScroll, messageContent, captureScrollAnchor, loadMessages, viewLatest, inspect, related, setTab,
  setDelivery, latestDeliveries, olderDeliveries}
const path = suffix => `/api/cockpit/scenes/${encodeURIComponent(sceneId.value)}${suffix}`
function setTab(value) {
  if (tabNames.includes(value)) router.push({
    name: 'scene',
    params: { sceneId: sceneId.value },
    query: {
      ...route.query,
      tab: value,
      event: undefined,
      page: undefined,
      before: undefined,
      snapshot: undefined
    }
  })
}
function setPage(value) {
  router.push({ query: { ...route.query, page: value === 1 ? undefined : String(value) } })
}
function inspect(event) {
  router.push({
    name: 'scene',
    params: { sceneId: sceneId.value },
    query: { ...route.query, event: event.id }
  })
}
function closeInspector() {
  router.push({ query: { ...route.query, event: undefined } })
}
function backToScenes() {
  router.push({ name: 'scenes', query: directoryQuery.value })
}
function filterDirectory(values = {}) {
  router.replace({
    query: {
      ...route.query,
      query: (sceneSearch.value || '').trim() || undefined,
      ...values
    }
  })
}
function sceneLocation(scene) {
  return {
    name: 'scene',
    params: { sceneId: scene.scene_id },
    query: {
      ...directoryQuery.value,
      ...(!scene.has_history && scene.scene_type === 'group' ? { tab: 'settings' } : {})
    }
  }
}
function related(target) {
  return withReturn(route, target)
}
function configureGroup() {
  const group = groupNumber.value.trim()
  if (!/^[1-9]\d*$/.test(group)) {
    groupError.value='请填写实际 QQ 群号';
    return
  }
  groupError.value=''
  router.push({
    name:'scene',
    params:{sceneId:`group:${group}`},
    query:{...directoryQuery.value,tab:'settings'}
  })
}
function setDelivery(value) {
  router.push({
    query: { ...route.query, delivery: value, before: undefined, snapshot: undefined }
  })
}
function olderDeliveries() {
  router.push({
    query: {
      ...route.query,
      before: String(aux.value.next_before),
      snapshot: String(aux.value.snapshot_rowid)
    }
  })
}
function latestDeliveries() {
  router.push({ query: { ...route.query, before: undefined, snapshot: undefined } })
}
async function loadDirectory() {
  directoryLoading.value = true;
  try {
    await loadScopes(true)
  } finally {
    directoryLoading.value = false
  }
}
async function loadDetail(reset = false) {
  const id = sceneId.value, own = ++detailRequest
  if (!id) return
  if (reset) {
    detail.value = null;
    detailReadAt.value = null
  }
  detailLoading.value = true;
  detailError.value = '';
  detailMissing.value = false
  try {
    const data = await api(path(''));
    if (own === detailRequest) {
      detail.value = data;
      detailReadAt.value = data.sampled_at
    }
  }
  catch (error) {
    if (own === detailRequest) {
      detailError.value = error.message;
      detailMissing.value = error.status === 404;
      if (detailMissing.value) detail.value = null
    }
  }
  finally {
    if (own === detailRequest) detailLoading.value = false
  }
}
function visibleTimeline() {
  return messageScroll.value && messageContent.value && tab.value === 'messages' && (!mobile.value || !eventId.value) && messageScroll.value.getClientRects().length
}
function captureScrollAnchor() {
  if (!visibleTimeline() || pendingPosition) return
  const area = messageScroll.value
  if (!mobile.value && area.scrollHeight - area.clientHeight - area.scrollTop < 4) {
    scrollAnchor = { mode: 'latest', scene: sceneId.value };
    return
  }
  const edge = mobile.value ? 0 : area.getBoundingClientRect().top
  const first = [...messageContent.value.querySelectorAll('[data-event-id]')].find(item => item.getBoundingClientRect().bottom > edge)
  if (first) scrollAnchor = {
    mode: 'event',
    scene: sceneId.value,
    id: first.dataset.eventId,
    offset: first.getBoundingClientRect().top - edge
  }
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
function sectionRoot() {
  return sceneId.value ? pageArea.value?.querySelector('.scene-main') : pageArea.value
}
function sectionRecords() {
  return [...(sectionRoot()?.querySelectorAll('[data-scene-record]') || [])]
}
function captureSectionPosition() {
  const focused = document.activeElement?.closest?.('[data-scene-record]')
  const records = sectionRecords()
  const target = records.includes(focused) ? focused : records.find(item => item.getBoundingClientRect().bottom > 0)
  return { top: window.scrollY, left: window.scrollX, record: target?.dataset.sceneRecord || '',
    offset: target?.getBoundingClientRect().top || 0, focus: target === focused }
}
function cancelSectionRestore() {
  sectionPosition = null
}
function restoreSectionPosition() {
  const position = sectionPosition
  if (!position || position.key !== sourcePath(route) || !pageArea.value) return
  if (sceneId.value ? detailLoading.value || auxLoading.value || (eventId.value && inspectorLoading.value) || (tab.value === 'settings' && !settingsReady.value) : directoryLoading.value) return
  sectionPosition = null
  const target = sectionRecords().find(item => item.dataset.sceneRecord === position.record)
  if (target) {
    window.scrollTo({
      left: position.left,
      top: window.scrollY + target.getBoundingClientRect().top - position.offset,
      behavior: 'instant'
    })
    if (position.focus) target.focus({ preventScroll: true })
  } else {
    window.scrollTo({
      left: position.left,
      top: position.record ? 0 : position.top,
      behavior: 'instant'
    })
    if (position.record) returnNotice.value = '返回范围已恢复，但未能在当前页面定位原条目；请核对读取错误或列表变化，未按旧位置定位其他条目。'
  }
}
function settingsLoaded(value) {
  if (value.sceneId !== sceneId.value || tab.value !== 'settings') return
  settingsReady.value = true
  if (!value.ok) cancelSectionRestore()
  nextTick(restoreSectionPosition)
}
function rememberVisit(from) {
  if (useAuth().status !== 'authenticated' || scalar(from.params.sceneId) !== sceneId.value) return
  if (sceneId.value && tab.value === 'messages') {
    if (!messageLoaded.value) return
    captureScrollAnchor()
    rememberSceneVisit(from, {
      scene: sceneId.value, tab: 'messages', messages: messages.value, snapshot: snapshot.value,
      nextBefore: nextBefore.value, hasMore: hasMore.value, historical: historical.value,
      readAt: messageReadAt.value, anchor: scrollAnchor ? { ...scrollAnchor } : null,
    })
  } else {
    rememberSceneVisit(from, { scene: sceneId.value, tab: sceneId.value ? tab.value : 'directory',
      position: captureSectionPosition(), participantSearch: participantSearch.value,
      expandedRecords: { ...expandedRecords.value } })
  }
}
function restoreVisit() {
  const saved = sceneVisit(route)
  if (!saved || saved.scene !== sceneId.value) return false
  if (!sceneId.value || tab.value !== 'messages') {
    if (!saved.position) return false
    participantSearch.value = saved.participantSearch || ''
    expandedRecords.value = { ...saved.expandedRecords }
    sectionPosition = { ...saved.position, key: sourcePath(route) }
    nextTick(restoreSectionPosition)
    return true
  }
  if (saved.tab !== 'messages') return false
  ++messageRequest
  messages.value = saved.messages;
  snapshot.value = saved.snapshot;
  nextBefore.value = saved.nextBefore
  hasMore.value = saved.hasMore;
  historical.value = saved.historical;
  messageReadAt.value = saved.readAt
  messageLoaded.value = true;
  messageLoading.value = false;
  olderLoading.value = false;
  newPage.value = null;
  messageError.value = ''
  scrollAnchor = saved.anchor ? { ...saved.anchor } : { mode: 'latest', scene: saved.scene }
  pendingPosition = true
  nextTick(restoreScrollAnchor)
  return true
}
onBeforeRouteLeave((to, from) => {
  rememberVisit(from)
})
onBeforeRouteUpdate((to, from) => {
  rememberVisit(from)
})
async function positionTimeline(anchor) {
  scrollAnchor = { ...anchor, scene: sceneId.value };
  pendingPosition = true
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
  const query = {
    limit: 50,
    ...(older ? { before: nextBefore.value, snapshot_rowid: snapshot.value } : {}),
    ...(locate ? { event_id: locate } : {})
  }
  try {
    const result = await api(`${path('/messages')}?${queryString(query)}`)
    if (own !== messageRequest) return
    const readAt = Date.now() / 1000
    if (refresh && messageLoaded.value) {
      if ((result.items[0]?.rowid || 0) > (messages.value.at(-1)?.rowid || 0)) newPage.value = { ...result, readAt }
      return
    }
    messageReadAt.value = readAt
    if (older) {
      const existing = new Set(messages.value.map(item => item.id))
      messages.value = [
        ...result.items.filter(item => !existing.has(item.id)).reverse(),
        ...messages.value
      ]
      historical.value = true
    } else {
      scrollAnchor = {
        mode: locate ? 'event' : 'latest',
        id: locate,
        offset: mobile.value ? 24 : 12,
        scene: id
      };
      pendingPosition = true
      messages.value = [...result.items].reverse();
      snapshot.value = result.snapshot_rowid;
      newPage.value = null;
      historical.value = Boolean(locate)
    }
    nextBefore.value = result.next_before;
    hasMore.value = result.has_more;
    messageLoaded.value = true
    await nextTick()
    restoreScrollAnchor()
  } catch (error) {
    if (own === messageRequest) messageError.value = error.message
  }
  finally {
    if (own === messageRequest) {
      messageLoading.value = false;
      olderLoading.value = false
    }
  }
}
async function viewLatest() {
  ++messageRequest
  if (!newPage.value) {
    await loadMessages();
    return
  }
  const result = newPage.value
  messages.value = [...result.items].reverse();
  snapshot.value = result.snapshot_rowid;
  nextBefore.value = result.next_before;
  hasMore.value = result.has_more;
  newPage.value = null;
  historical.value = false;
  messageLoading.value = false;
  olderLoading.value = false
  messageReadAt.value = result.readAt
  await positionTimeline({ mode: 'latest' })
}
async function loadInspector({ locate = true } = {}) {
  const id = eventId.value, scene = sceneId.value, own = ++inspectorRequest
  if (!id || !scene) {
    selectedEvent.value = null;
    relations.value = null;
    inspectorLoading.value = false;
    inspectorError.value = '';
    inspectorReadAt.value = null;
    return
  }
  const sameEvent = selectedEvent.value?.id === id && selectedEvent.value?.scene_id === scene
  const previousReadAt = inspectorReadAt.value
  inspectorLoading.value = true;
  inspectorError.value = ''
  if (!sameEvent) {
    selectedEvent.value = null;
    relations.value = null;
    inspectorReadAt.value = null
  }
  try {
    const [eventResult, relationResult] = await Promise.allSettled([
      api(`/api/cockpit/events/${encodeURIComponent(id)}?${queryString({ scene_id: scene })}`),
      api(`/api/cockpit/relations?${queryString({ scene_id: scene, event_id: id })}`),
    ])
    if (own !== inspectorRequest) return
    if (eventResult.status === 'rejected') throw eventResult.reason
    selectedEvent.value = eventResult.value;
    inspectorReadAt.value = Date.now() / 1000
    if (relationResult.status === 'fulfilled') relations.value = relationResult.value
    else inspectorError.value = `关联读取失败：${relationResult.reason.message}${relations.value && previousReadAt ? '；关联保留 ' + fmtTime(previousReadAt) + ' 的记录。' : ''}`
    if (locate && tab.value === 'messages' && ['GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED', 'MESSAGE_SENT'].includes(eventResult.value.event_type)) {
      if (!messages.value.some(item => item.id === id)) await loadMessages({ locate: id })
      else await scrollToMessage(id)
    }
  } catch (error) {
    if (own === inspectorRequest) inspectorError.value = error.status === 404 ? '事件不存在或不属于当前场景。' : error.message
  }
  finally {
    if (own === inspectorRequest) inspectorLoading.value = false
  }
}
async function loadAux(reset = false) {
  const id = sceneId.value, selectedTab = tab.value, own = ++auxRequest
  if (reset) {
    aux.value = null;
    auxReadAt.value = null
  }
  auxError.value = '';
  auxLoading.value = false
  if (!id || !['attention', 'history', 'memory', 'jobs', 'deliveries'].includes(selectedTab)) return
  auxLoading.value = true
  let url
  const query = queryString({ scene_id: id, page: page.value, page_size: 30 })
  if (selectedTab === 'attention') url = `${path('/pending-wakes')}?${queryString({ page: page.value, page_size: 30 })}`
  if (selectedTab === 'history') url = `/api/cockpit/history-batches?${query}`
  if (selectedTab === 'jobs') url = `/api/cockpit/jobs?${query}`
  if (selectedTab === 'memory') url = `/api/cockpit/memories?${queryString({
    scope: id,
    status: 'active',
    validity: 'current',
    page: page.value,
    page_size: 30
  })}`
  if (selectedTab === 'deliveries') url = `/api/cockpit/events?${queryString({
    scene_id: id,
    event_type: deliveryType.value,
    limit: 50,
    before: scalar(route.query.before),
    snapshot_rowid: scalar(route.query.snapshot)
  })}`
  try {
    const result = await api(url);
    if (own === auxRequest) {
      aux.value = result;
      auxReadAt.value = Date.now() / 1000
    }
  }
  catch (error) {
    if (own === auxRequest) auxError.value = error.message
  }
  finally {
    if (own === auxRequest) auxLoading.value = false
  }
}
async function refresh() {
  const requests = [loadDirectory()]
  if (sceneId.value) {
    requests.push(loadDetail());
    if (tab.value === 'messages') requests.push(loadMessages({ refresh: true })); else requests.push(loadAux());
    if (eventId.value) requests.push(loadInspector({ locate: false }))
  }
  await Promise.all(requests)
}
function onVisible() {
  if (document.visibilityState === 'visible') refresh()
}
onMounted(() => {
  loadDirectory();
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('scroll', captureScrollAnchor, { passive: true })
})
onBeforeUnmount(() => {
  ++viewRequest;
  ++detailRequest;
  ++messageRequest;
  ++inspectorRequest;
  ++auxRequest;
  disposeRetry();
  contentObserver?.disconnect();
  document.removeEventListener('visibilitychange', onVisible);
  window.removeEventListener('scroll', captureScrollAnchor)
})
watch([directoryLoading, detailLoading, auxLoading, inspectorLoading], () => nextTick(restoreSectionPosition), { flush: 'post' })
watch(messageContent, async content => {
  contentObserver?.disconnect()
  if (!content) return
  contentObserver = new ResizeObserver(restoreScrollAnchor)
  contentObserver.observe(content)
  await nextTick()
  restoreScrollAnchor()
}, { flush: 'post' })
watch(() => route.query.query, value => {
  sceneSearch.value = scalar(value)
}, { immediate: true })
watch(() => sourcePath(route), async () => {
  const own = ++viewRequest, changedScene = activeScene !== sceneId.value
  const previousTab = activeTab, previousEvent = activeEvent
  cancelRetry()
  sectionPosition = null;
  returnNotice.value = ''
  expandedRecords.value = {}
  if (changedScene || previousTab !== tab.value) settingsReady.value = false
  activeScene = sceneId.value;
  activeTab = tab.value;
  activeEvent = eventId.value
  if (changedScene) {
    stopRetryPoll()
    ++detailRequest;
    ++messageRequest;
    ++inspectorRequest
    scrollAnchor = null;
    pendingPosition = false
    detailLoading.value = false;
    messageLoading.value = false;
    olderLoading.value = false;
    inspectorLoading.value = false
    detail.value = null;
    detailError.value = '';
    detailMissing.value = false;
    detailReadAt.value = null
    messages.value = [];
    messageLoaded.value = false;
    messageError.value = '';
    messageReadAt.value = null;
    newPage.value = null;
    snapshot.value = null;
    nextBefore.value = null;
    hasMore.value = false;
    historical.value = false
    selectedEvent.value = null;
    relations.value = null;
    inspectorError.value = '';
    inspectorReadAt.value = null
    retryConfirmation.value = null;
    retryError.value = '';
    feedback.value = '';
    participantSearch.value = ''
  }
  if (!sceneId.value) {
    restoreVisit();
    return
  }
  if (changedScene) loadDetail(true)
  const restored = restoreVisit()
  if (changedScene || previousEvent !== eventId.value || previousTab !== tab.value) await loadInspector({ locate: !restored })
  if (own !== viewRequest || tab.value !== 'messages') return
  if (!messageLoaded.value && !messageLoading.value) await loadMessages()
  else if (!restored && !eventId.value && previousEvent) await scrollToMessage(previousEvent)
  else {
    await nextTick();
    restoreScrollAnchor()
  }
}, { immediate: true })
watch(() => [
  sceneId.value,
  tab.value,
  page.value,
  deliveryType.value,
  route.query.before,
  route.query.snapshot
], () => loadAux(true), { immediate: true })
</script>

<template>
  <section
    ref="pageArea"
    class="scenes-page"
    @wheel.passive="cancelSectionRestore"
    @pointerdown="cancelSectionRestore"
    @keydown="cancelSectionRestore"
  >
    <PageHeader title="群聊工作台" description="在同一个群内查看对话、记忆、工作和设置。刷新只读取记录。">
      <v-btn
        :prepend-icon="mdiRefresh"
        variant="outlined"
        :loading="directoryLoading || detailLoading || messageLoading || auxLoading"
        :disabled="olderLoading"
        @click="refresh"
      >刷新记录</v-btn>
    </PageHeader>
    <v-alert v-if="appState.sceneError" type="error" variant="tonal" class="mb-4">群聊目录读取失败：{{ appState.sceneError }}<p v-if="appState.sceneReadAt">保留 {{ fmtTime(appState.sceneReadAt) }} 的目录。</p>
    </v-alert>
    <v-alert v-else-if="appState.discoveryError" type="warning" variant="tonal" class="mb-4">已加入群列表读取失败，保留已知群：{{ appState.discoveryError }}
    </v-alert>
    <v-alert v-if="returnNotice" type="info" variant="tonal" class="mb-4">
      {{ returnNotice }}
    </v-alert>
    <div
      class="scene-workspace"
      :class="{ 'has-scene': sceneId, 'has-inspector': eventId, 'wide-workspace': wide }"
    >
      <v-card v-show="!mobile || !sceneId" class="scene-directory">
        <v-card-text class="directory-search">
          <v-form class="directory-filters" @submit.prevent="filterDirectory()">
            <v-text-field
              v-model="sceneSearch"
              :prepend-inner-icon="mdiMagnify"
              label="查找名称或号码"
              hide-details
              clearable
              @click:clear="filterDirectory({query:undefined})"
            />
            <v-select
              :model-value="directoryType"
              label="会话类型"
              hide-details
              :items="[{title:'群聊与私聊',value:'all'},{title:'仅群聊',value:'group'},{title:'仅私聊',value:'private'}]"
              @update:model-value="value => filterDirectory({type:value==='all'?undefined:value,permission:undefined})"
            />
            <v-select
              v-if="directoryType!=='private'"
              :model-value="permission"
              :items="permissionOptions"
              label="按已保存设置筛选"
              hide-details
              @update:model-value="value => filterDirectory({permission:value==='all'?undefined:value})"
            />
            <v-btn type="submit" variant="tonal" size="small">筛选目录</v-btn>
          </v-form>
          <v-expansion-panels class="mt-3">
            <v-expansion-panel title="配置另一个群">
              <v-expansion-panel-text>
                <p class="muted-copy mb-3">只打开设置草稿，不会加入群或启用能力。</p>
                <v-form @submit.prevent="configureGroup">
                  <v-text-field
                    v-model="groupNumber"
                    label="QQ 群号"
                    inputmode="numeric"
                    :error-messages="groupError"
                  />
                  <v-btn type="submit" color="primary" variant="tonal">打开本群设置</v-btn>
                </v-form>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
        </v-card-text>
        <v-list class="scene-list" lines="three" aria-label="场景目录">
          <v-list-item
            v-for="scene in scenes"
            :key="scene.scene_id"
            :data-scene-record="'directory:' + scene.scene_id"
            :to="sceneLocation(scene)"
            :active="scene.scene_id === sceneId"
            color="primary"
            class="scene-list-row"
          >
            <div class="scene-list-content">
              <strong>{{ scene.display_name }}</strong>
              <span class="scene-id">{{ scene.scene_id }}</span>
              <div v-if="scene.scene_type==='group'" class="record-meta">
                <StatusBadge domain="scene_chat" :status="chatMode(scene)" />
                <span>{{ scene.joined===true?'已加入':scene.joined===false?'未确认加入':'加入状态未知' }}</span>
              </div>
              <time>{{ scene.has_history?'最近活动 '+fmtTime(scene.last_event_at):'尚无原话记录' }}</time>
              <span v-if="workEnabled(scene)" class="scene-id">工作插件已开启 · 执行条件另行核对</span>
              <div class="scene-counts">
                <span>待处理 {{ scene.pending_wake_count }}</span>
                <span>工作 {{ scene.active_job_count }}</span>
              </div>
            </div>
          </v-list-item>
        </v-list>
        <v-progress-linear v-if="directoryLoading" indeterminate aria-label="正在读取群聊目录" />
        <p
          v-if="appState.loadedScenes && !scenes.length && !appState.sceneError"
          class="empty-copy"
        >没有符合当前筛选的会话。</p>
        <p class="directory-count">
          {{ scenes.length }} 个会话<span v-if="appState.sceneReadAt"> · 目录读取于 {{ fmtTime(appState.sceneReadAt) }}</span>
        </p>
      </v-card>
      <v-card v-if="!sceneId && !mobile" class="scene-placeholder">
        <v-icon :icon="mdiMessageOutline" size="40" />
        <h2>选择一个群聊或私聊</h2>
        <p>查看原话与关联记录，或在唯一设置页修改本群规则。</p>
        <p class="muted-copy">目录显示已保存设置，不代表工具已经执行或文件已经送达。</p>
      </v-card>
      <div v-if="sceneId" v-show="!mobile || !eventId" class="scene-main">
        <v-btn
          v-if="mobile"
          variant="text"
          :prepend-icon="mdiArrowLeft"
          class="mb-3"
          @click="backToScenes"
        >返回群聊目录</v-btn>
        <v-alert
          v-if="detailError&&tab!=='settings'"
          :type="detailMissing ? 'warning' : 'error'"
          variant="tonal"
          class="mb-4"
        >
          {{ detailError }}<p v-if="detailReadAt">保留上次读取：{{ fmtTime(detailReadAt) }}</p>
        </v-alert>
        <v-skeleton-loader v-if="detailLoading && !detail" type="list-item-two-line" />
        <template v-if="sceneId">
          <v-card class="scene-heading">
            <v-card-text>
              <h2>{{ currentScene?.display_name || detail?.session.display_name || sceneId }}</h2>
              <div class="scene-heading-meta">
                <EntityLink type="scene" :id="sceneId" :scene-id="sceneId" :label="sceneId" />
                <span v-if="detail">
                  {{ Object.keys(detail.session.participants).length }} 位已记录成员</span>
                <span v-if="detailReadAt">群状态读取于 {{ fmtTime(detailReadAt) }}</span>
              </div>
              <div class="scene-facts">
                <div v-if="sceneId.startsWith('group:')">
                  <span class="fact-label">已保存设置</span>
                  <StatusBadge
                    v-if="currentScene"
                    domain="scene_chat"
                    :status="chatMode(currentScene)"
                  />
                  <span v-else>尚未取得</span>
                  <small v-if="appState.sceneReadAt">{{ fmtTime(appState.sceneReadAt) }}</small>
                </div>
                <div>
                  <span class="fact-label">当前运行</span>
                  <strong>
                    {{ appState.error?'状态读取失败':!appState.status?'尚未取得':appState.status.running?'运行时已启动':'运行时已停止' }}
                  </strong>
                  <small v-if="appState.status">
                    {{ appState.status.shadow_mode?'Shadow，仅记录候选':'按各群规则发送' }} · {{ fmtTime(appState.status.sampled_at) }}
                  </small>
                </div>
                <div>
                  <span class="fact-label">最近实际发言</span>
                  <strong>{{ fmtTime(detail?.session.last_bot_message_at) }}</strong>
                  <small>本条送达状态以原始回执为准</small>
                </div>
              </div>
              <div class="scene-quick-links">
                <v-btn v-if="currentScene" size="small" variant="text" @click="setTab('attention')">待处理来源 {{ currentScene.pending_wake_count }}
                </v-btn>
                <v-btn v-if="currentScene" size="small" variant="text" @click="setTab('jobs')">未完结工作 {{ currentScene.active_job_count }}
                </v-btn>
                <v-btn
                  size="small"
                  variant="tonal"
                  :to="related({name:'capabilities',query:{scene:sceneId}})"
                >查看能力条件</v-btn>
              </div>
            </v-card-text>
            <div class="workspace-tabs">
              <v-tabs
                :model-value="selectedDiagnostic?null:tab"
                :mandatory="true"
                color="primary"
                show-arrows
                @update:model-value="setTab"
              >
                <v-tab
                  v-for="item in primaryTabs"
                  :key="item.value"
                  v-show="item.value!=='settings'||sceneId.startsWith('group:')"
                  :value="item.value"
                >
                  {{ item.value==='memory'&&sceneId.startsWith('private:')?'本会话记忆':item.label }}
                </v-tab>
              </v-tabs>
              <v-menu>
                <template #activator="{props}">
                  <v-btn
                    v-bind="props"
                    :append-icon="mdiChevronDown"
                    :color="selectedDiagnostic?'primary':undefined"
                    :variant="selectedDiagnostic?'tonal':'text'"
                    class="diagnostic-menu"
                  >
                    {{ selectedDiagnostic?.label || '更多诊断' }}
                  </v-btn>
                </template>
                <v-list aria-label="更多群聊诊断">
                  <v-list-item
                    v-for="item in diagnosticTabs"
                    :key="item.value"
                    :active="tab===item.value"
                    :title="item.label"
                    @click="setTab(item.value)"
                  />
                </v-list>
              </v-menu>
            </div>
          </v-card>
          <v-alert v-if="feedback" type="success" variant="tonal" class="mt-4" role="status">
            {{ feedback }}
          </v-alert>
          <v-card v-if="tab==='settings'" class="scene-tab-card">
            <v-card-text>
              <SceneSettingsForm
                :scene-id="sceneId"
                @saved="loadDirectory"
                @loaded="settingsLoaded"
              />
            </v-card-text>
          </v-card>
          <v-card
            v-else-if="!detail && !['memory','jobs'].includes(tab) && !(tab==='messages'&&messageLoaded)"
            class="scene-tab-card"
          >
            <v-card-text>
              <p class="muted-copy">
                {{ detailMissing?'此会话尚无可读取的原话状态，配置群规则不需要先制造聊天记录。':detailLoading?'正在读取群状态…':'尚未取得群状态，请刷新后再查看。' }}
              </p>
              <v-btn
                v-if="sceneId.startsWith('group:')"
                color="primary"
                variant="tonal"
                class="mt-4"
                @click="setTab('settings')"
              >打开本群设置</v-btn>
            </v-card-text>
          </v-card>
          <SceneMessagesTab v-else-if="tab === 'messages'" :page="shared" />
          <v-card v-else class="scene-tab-card">
            <v-card-text class="scene-detail-body">
              <v-progress-linear v-if="auxLoading" indeterminate aria-label="正在读取场景详情" />
              <v-alert v-if="auxError" type="error" variant="tonal" class="mb-4">
                {{ auxError }}<div v-if="auxReadAt">上次读取于 {{ fmtTime(auxReadAt) }}</div>
              </v-alert>
              <SceneMemoryTab v-if="tab === 'memory'" :page="shared" />
              <SceneAttentionTab v-else-if="tab === 'attention'" :page="shared" />
              <SceneHistoryTab v-else-if="tab === 'history'" :state="retryState" :page="shared" />
              <SceneParticipantsTab v-else-if="tab === 'participants'" :page="shared" />
              <ScenePreferencesTab v-else-if="tab === 'preferences'" :page="shared" />
              <SceneJobsTab v-else-if="tab === 'jobs'" :page="shared" />
              <SceneDeliveriesTab v-else-if="tab === 'deliveries'" :page="shared" />
              <div
                v-if="aux && ['attention', 'history', 'memory', 'jobs'].includes(tab)"
                class="aux-pagination"
              >
                <span>共 {{ aux.total }} 项 · 第 {{ aux.page }} 页<span v-if="auxReadAt"> · 读取于 {{ fmtTime(auxReadAt) }}</span>
                </span>
                <v-pagination
                  v-if="auxPages > 1"
                  :model-value="page"
                  :length="auxPages"
                  :total-visible="3"
                  @update:model-value="setPage"
                />
              </div>
            </v-card-text>
          </v-card>
        </template>
      </div>
      <v-card v-if="sceneId && eventId && (wide || mobile)" class="inline-inspector">
        <SceneInspector
          :active="Boolean(eventId)"
          :event="selectedEvent"
          :relations="relations"
          :loading="inspectorLoading"
          :error="inspectorError"
          :read-at="inspectorReadAt"
          @close="closeInspector"
          @retry="loadInspector"
        />
      </v-card>
    </div>
    <v-navigation-drawer
      v-if="sceneId && !mobile && !wide"
      :model-value="Boolean(eventId)"
      temporary
      location="right"
      width="420"
      aria-label="消息关联检查抽屉"
      @update:model-value="value => { if (!value && eventId) closeInspector() }"
    >
      <SceneInspector
        modal
        :active="Boolean(eventId)"
        :event="selectedEvent"
        :relations="relations"
        :loading="inspectorLoading"
        :error="inspectorError"
        :read-at="inspectorReadAt"
        @close="closeInspector"
        @retry="loadInspector"
      />
    </v-navigation-drawer>
    <RetryHistoryDialog :state="retryState" />
  </section>
</template>
<style scoped>
.directory-filters{display:grid;gap:12px}
.workspace-tabs{display:flex;align-items:center;gap:8px;flex-wrap:wrap;border-top:1px solid var(--line);padding:0 12px}
.workspace-tabs>.v-tabs{flex:1;min-width:0}
.diagnostic-menu{margin-block:6px;max-width:100%}
.scene-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-top:20px}
.scene-facts>div{display:flex;align-items:flex-start;flex-direction:column;gap:6px;min-width:0;font-size:13px}
.scene-facts small,.fact-label{color:var(--muted);font-size:12px;line-height:1.6;overflow-wrap:anywhere}
.scene-quick-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.scene-quick-links>.v-btn{max-width:100%}
@media(max-width:650px){
  .workspace-tabs{display:block;padding:0 4px}
  .workspace-tabs>.v-tabs{width:100%}
  .diagnostic-menu{margin-left:8px}
  .scene-facts{grid-template-columns:1fr}
  .scene-quick-links{gap:4px}
}
.scenes-page{min-width:0}
.scenes-page>.page-header{margin-bottom:24px}
.scene-workspace{display:grid;grid-template-columns:248px minmax(0,1fr);gap:18px;align-items:start;min-width:0}
.scene-workspace.wide-workspace.has-scene.has-inspector{grid-template-columns:240px minmax(0,1fr) 340px}
.scene-directory,.scene-main,.scene-tab-card,.scene-heading,.inline-inspector{min-width:0}
.scene-directory{overflow:hidden}
.directory-search{padding:16px}
.scene-list{padding:0 8px}
.scene-list-row{margin-bottom:4px;border-radius:8px;min-width:0}
.scene-list-row :deep(.v-list-item__content){min-width:0}
.scene-list-content{display:flex;flex-direction:column;gap:6px;min-width:0;padding:8px 0}
.scene-list-content strong{font-size:14px;line-height:1.6;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.scene-id{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.scene-list-content time,.scene-counts,.directory-count{font-size:11px;color:var(--muted)}
.scene-counts{display:flex;gap:14px;flex-wrap:wrap}
.directory-count{padding:12px 20px}
.scene-placeholder{min-height:360px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;color:var(--muted);padding:24px;text-align:center}
.scene-placeholder h2{font-size:18px;color:var(--ink)}
.scene-heading h2{font-size:20px;line-height:1.6;overflow-wrap:anywhere}
.scene-heading-meta{display:flex;gap:8px 16px;flex-wrap:wrap;margin-top:8px;align-items:center;font-size:12px;color:var(--muted);min-width:0}
.scene-heading-meta>*{min-width:0}
.scene-tab-card{margin-top:16px}
.scene-detail-body{min-width:0;line-height:1.7}
.scene-detail-body p{margin:10px 0;overflow-wrap:anywhere}
.muted-copy{font-size:13px;color:var(--muted);line-height:1.8}
.record-meta{display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.record-meta>*{min-width:0}
.record-meta strong{color:var(--ink);overflow-wrap:anywhere}
.empty-copy{padding:28px 16px;text-align:center;font-size:13px;color:var(--muted);line-height:1.8}
.aux-pagination{margin-top:20px;color:var(--muted);font-size:12px}
.inline-inspector{max-height:calc(100vh - 180px);overflow:hidden;position:sticky;top:20px}
.inline-inspector :deep(.scene-inspector){max-height:calc(100vh - 180px)}
@media(max-width:1200px){
  .scene-workspace{grid-template-columns:220px minmax(0,1fr);gap:14px}
  .scene-heading h2{font-size:18px}
}
@media(max-width:1023px){
  .scene-workspace,.scene-workspace.wide-workspace.has-scene.has-inspector{grid-template-columns:minmax(0,1fr)}
  .scene-list-row{min-height:unset}
  .scene-list-content{padding:10px 0}
  .scene-heading h2{font-size:18px}
  .scene-heading-meta{align-items:flex-start;flex-direction:column}
  .inline-inspector{max-height:none;position:static}
  .inline-inspector :deep(.scene-inspector){max-height:none}
  .inline-inspector :deep(.inspector-body){overflow:visible}
  .scene-detail-body{padding:16px}
  .scene-counts{gap:18px}
  .directory-search{padding:16px}
}
</style>
