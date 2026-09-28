<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, queryString, sceneName } from '../api.js'
import { useRequestGuard } from '../composables/useRequestGuard.js'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const route = useRoute(), router = useRouter()
const state = ref(null), items = ref([]), nextOffset = ref(null)
const stateLoading = ref(false), listLoading = ref(false), creating = ref(false), cancelling = ref(null)
const stateError = ref(''), listError = ref(''), createError = ref(''), cancelError = ref('')
const createNotice = ref(''), cancelNotice = ref('')
const form = ref({ requester: '', when: '', note: '', for: 'self' })
const cancelRequester = ref('')
const statuses = [
  { title: '待处理（含受阻）', value: 'active' }, { title: '全部', value: 'all' },
  { title: '待交付', value: 'pending' }, { title: '受阻', value: 'blocked' },
  { title: '已交付', value: 'delivered' }, { title: '已取消', value: 'cancelled' },
]
const selectedScene = computed(() => typeof route.query.scene === 'string' ? route.query.scene : '')
const status = computed(() => typeof route.query.status === 'string' ? route.query.status : 'active')
const selectedSettings = computed(() => state.value?.scenes.find(item => item.scene === selectedScene.value) || null)
const dirty = computed(() => form.value.requester !== '' || form.value.when !== '' || form.value.note !== '' ||
  form.value.for !== 'self' || cancelRequester.value !== '')
useUnsavedChanges(dirty)
onBeforeRouteUpdate(to => {
  if (to.query.scene === route.query.scene && to.query.status === route.query.status) return true
  if (creating.value || cancelling.value !== null) return false
  return !dirty.value || window.confirm('有未提交的安排草稿。放弃草稿并切换场景或筛选？')
})
const beginState = useRequestGuard()
const beginList = useRequestGuard(() => `${selectedScene.value}\u0000${status.value}`)
const beginCreate = useRequestGuard(() => selectedScene.value)
const beginCancel = useRequestGuard(() => selectedScene.value)

function time(value, zone) {
  if (value === null || value === undefined || !zone) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', {
    timeZone: zone, timeZoneName: 'short', hour12: false,
  })
}
function recurring(item) {
  return item.interval_seconds !== null || item.cron_minute_of_day !== null
}
function cronExpression(item) {
  const hour = Math.floor(item.cron_minute_of_day / 60)
  const minute = item.cron_minute_of_day % 60
  return `cron:${minute} ${hour} * * *`
}
function cadence(item) {
  if (item.cron_minute_of_day !== null) {
    const hour = String(Math.floor(item.cron_minute_of_day / 60)).padStart(2, '0')
    const minute = String(item.cron_minute_of_day % 60).padStart(2, '0')
    return `每日 ${hour}:${minute} · ${item.timezone}`
  }
  const value = item.interval_seconds
  if (value === null) return '一次性'
  if (value % 86400 === 0) return `每 ${value / 86400} 天 · 固定 UTC 秒`
  if (value % 3600 === 0) return `每 ${value / 3600} 小时 · 固定 UTC 秒`
  return `每 ${value / 60} 分钟 · 固定 UTC 秒`
}
function statusLabel(value) {
  return { pending:'待交付', blocked:'受阻', delivered:'已交付会话', cancelled:'已取消' }[value] || value
}
function dueLabel(item) {
  if (item.status === 'blocked') return '已保存原定时刻（未安排后续）'
  if (item.status === 'cancelled') return '取消前原定时间'
  if (recurring(item) && item.status === 'pending') return '下次到期'
  return '原定时间'
}
function operationError(error, action) {
  return error.status >= 400 && error.status < 500
    ? `${action}未被接受：${error.message}`
    : `${action}结果未确认：${error.message} 草稿已保留；请手动刷新核对，不会自动重试。`
}
function resetDraft() {
  form.value = { requester:'', when:'', note:'', for:'self' }
  cancelRequester.value = ''
  createError.value = ''; cancelError.value = ''
  createNotice.value = ''; cancelNotice.value = ''
}
async function readState() {
  if (creating.value || cancelling.value !== null) return
  const fresh = beginState()
  stateLoading.value = true
  try {
    const value = await api('/api/host/schedules/state')
    if (!fresh()) return
    state.value = value; stateError.value = ''
    if (!value.scenes.some(item => item.scene === selectedScene.value) && value.scenes.length) {
      await router.replace({ name:'host-schedules', query:{ scene:value.scenes[0].scene, status:status.value } })
    } else if (selectedScene.value) {
      await readList()
    }
  } catch (error) {
    if (fresh()) stateError.value = error.message
  } finally {
    if (fresh()) stateLoading.value = false
  }
}
async function readList(more = false) {
  if (!selectedScene.value || listLoading.value || (more && nextOffset.value === null)) return
  const offset = more ? nextOffset.value : 0
  const fresh = beginList()
  listLoading.value = true
  try {
    const page = await api(`/api/host/schedules?${queryString({ scene:selectedScene.value, status:status.value, offset, limit:20 })}`)
    if (!fresh()) return
    items.value = more ? [...items.value, ...page.items] : page.items
    nextOffset.value = page.next_offset
    listError.value = ''
  } catch (error) {
    if (fresh()) listError.value = error.message
  } finally {
    if (fresh()) listLoading.value = false
  }
}
function chooseScene(value) {
  if (value && value !== selectedScene.value) router.push({ name:'host-schedules', query:{ scene:value, status:status.value } })
}
function chooseStatus(value) {
  if (value !== status.value) router.push({ name:'host-schedules', query:{ scene:selectedScene.value, status:value } })
}
async function create() {
  if (!selectedSettings.value || creating.value || cancelling.value !== null) return
  const fresh = beginCreate(), scene = selectedScene.value
  creating.value = true; createError.value = ''; createNotice.value = ''
  try {
    await api(`/api/host/schedules?${queryString({ scene })}`, {
      method:'POST', body:JSON.stringify({
        requester:form.value.requester, when:form.value.when, note:form.value.note, for:form.value.for,
      }),
    })
    if (!fresh()) return
    form.value = { requester:'', when:'', note:'', for:'self' }
    createNotice.value = '安排已保存；尚未交付会话或发送提醒。'
    await readList()
  } catch (error) {
    if (fresh()) createError.value = operationError(error, '创建')
  } finally {
    if (fresh()) creating.value = false
  }
}
async function cancel(item) {
  if (!selectedSettings.value || cancelling.value !== null || creating.value) return
  if (!cancelRequester.value) { cancelError.value = '请填写实际操作者 QQ。'; return }
  if (!window.confirm(recurring(item)
    ? `停止此周期安排后续唤醒？已交付的过去次数不会撤回。\n${item.note}`
    : `取消这条一次性安排？\n${item.note}`)) return
  const fresh = beginCancel(), scene = selectedScene.value
  cancelling.value = item.id; cancelError.value = ''; cancelNotice.value = ''
  try {
    await api(`/api/host/schedules/${item.id}/cancel?${queryString({ scene })}`, {
      method:'POST', body:JSON.stringify({ requester:cancelRequester.value }),
    })
    if (!fresh()) return
    cancelRequester.value = ''
    cancelNotice.value = recurring(item)
      ? '取消已保存；此周期安排后续唤醒已停止，过去交付未撤回。'
      : '取消已保存；这条一次性安排不再交付会话。'
    await readList()
  } catch (error) {
    if (fresh()) cancelError.value = operationError(error, '取消')
  } finally {
    if (fresh()) cancelling.value = null
  }
}
watch([selectedScene, status], ([scene, filter], [oldScene, oldFilter]) => {
  if (scene === oldScene && filter === oldFilter) return
  beginList()
  resetDraft()
  items.value = []; nextOffset.value = null; listError.value = ''; listLoading.value = false
  if (state.value && selectedSettings.value) readList()
})
onMounted(readState)
</script>

<template>
  <div class="page-stack host-schedules">
    <header class="page-intro"><div><p class="eyebrow">独立多场景宿主</p><h1>场景安排</h1>
      <p class="muted">查看真实已保存的一次性提醒、固定秒数间隔与每日本地钟点安排。创建和取消都需要填写实际操作者 QQ；面板账户不代替聊天身份。</p></div>
      <v-btn variant="outlined" :loading="stateLoading || listLoading" :disabled="stateLoading || listLoading || creating || cancelling!==null" @click="readState">手动刷新</v-btn></header>
    <v-alert v-if="stateError" type="error" variant="tonal" role="alert" :title="state?'场景状态读取失败 · 保留上次快照':'场景状态读取失败'">{{ stateError }}</v-alert>
    <p v-if="stateLoading && !state" role="status">正在读取场景安排状态…</p>
    <template v-if="state">
      <p v-if="!state.scenes.length" class="surface muted">当前宿主没有已配置的安排场景。</p>
      <section class="surface"><div class="filters">
        <v-select :model-value="selectedScene" :items="state.scenes.map(item=>({title:sceneName(item.scene),value:item.scene}))" label="场景" hide-details="auto" :disabled="creating || cancelling!==null" @update:model-value="chooseScene" />
        <v-select :model-value="status" :items="statuses" label="列表筛选" hide-details="auto" :disabled="creating || cancelling!==null" @update:model-value="chooseStatus" />
      </div>
      <template v-if="selectedSettings"><p class="muted">{{ sceneName(selectedScene) }} · {{ state.timezone }} · 最多 {{ selectedSettings.max_pending }} 条待处理安排。{{ selectedSettings.enabled?'当前场景允许创建':'当前场景已关闭创建与到期执行' }}；{{ selectedSettings.tool_allowed?'角色已允许安排工具':'角色未开放安排工具' }}。这些是最近读取的运行配置。</p>
        <p class="muted">本人、他人及管理权限按实际 QQ 和场景身份判断；不会自动填入配置中的主人 QQ。</p></template>
      </section>
      <section v-if="selectedSettings" class="surface" aria-labelledby="create-title"><h2 id="create-title">创建安排</h2>
        <p class="muted">一次性时间须带 UTC 偏移，如 2026-10-01T09:00:00+08:00。固定秒数周期写 <code>every 30m</code>、<code>every 2h</code> 或 <code>every 1d</code>（1 分钟至 365 天，d=24 小时）；每日本地钟点写 <code>cron:0 20 * * *</code>（当前场景 {{ state.timezone }} 的 20:00）。cron 仅支持固定分钟与小时、每天执行，不支持列表、范围、星期或每月。</p>
        <p class="muted">遇夏令时跳过或重复的本地钟点不会猜测偏移：创建时明确拒绝，后续推进冲突会受阻并保留原因。需要特定真实时刻时可使用带偏移的一次性时间；不会把每日钟点自动改成 24 小时间隔。</p>
        <form @submit.prevent="create"><div class="form-grid">
          <v-text-field v-model="form.requester" label="实际请求人 QQ" inputmode="numeric" required hide-details="auto" :disabled="creating" />
          <v-text-field v-model="form.for" label="对象：self 或实际 QQ" required hide-details="auto" :disabled="creating" />
          <v-text-field v-model="form.when" label="何时执行（带偏移 ISO、every 或每日 cron）" required hide-details="auto" :disabled="creating" />
        </div><v-textarea v-model="form.note" label="安排原文" rows="3" auto-grow required hide-details="auto" :disabled="creating" />
          <v-alert v-if="createError" type="error" variant="tonal" role="alert">{{ createError }}</v-alert>
          <p v-if="createNotice" class="success-note" role="status">{{ createNotice }}</p>
          <v-btn type="submit" color="primary" :loading="creating" :disabled="!selectedSettings.enabled || !selectedSettings.tool_allowed || cancelling!==null || listLoading || stateLoading">保存安排</v-btn>
        </form>
      </section>
      <section v-if="selectedSettings" class="surface" aria-labelledby="list-title"><div class="section-heading"><h2 id="list-title">已保存安排</h2><span class="muted">按保存的到期时间排序 · 每页最多 20 条</span></div>
        <p class="muted">“已交付”只表示进入会话，不等于已向 QQ 发出或对方收到。受阻仍属于待处理；周期待交付项显示下次到期，受阻项只保留已确定的原定时刻，不表示已安排下一次。</p>
        <v-alert v-if="listError" type="error" variant="tonal" role="alert" :title="items.length?'列表读取失败 · 保留上次结果':'列表读取失败'">{{ listError }}</v-alert>
        <p v-if="listLoading && !items.length" role="status">正在读取安排…</p>
        <p v-else-if="!listLoading && !items.length && !listError" class="muted">此筛选下没有已保存安排。</p>
        <v-alert v-if="cancelError" type="error" variant="tonal" role="alert">{{ cancelError }}</v-alert>
        <p v-if="cancelNotice" class="success-note" role="status">{{ cancelNotice }}</p>
        <template v-if="items.length"><div class="cancel-operator"><v-text-field v-model="cancelRequester" label="取消时的实际操作者 QQ" inputmode="numeric" hide-details="auto" :disabled="cancelling!==null" /><span class="muted">本人或有管理权限的 QQ 可取消；不以面板登录账户代填。</span></div>
          <ul class="schedule-list"><li v-for="item in items" :key="item.id" class="schedule-card">
            <div class="card-heading"><strong>{{ statusLabel(item.status) }}</strong><span>{{ cadence(item) }}</span></div>
            <p v-if="item.cron_minute_of_day!==null" class="muted">每日钟点表达式：<code>{{ cronExpression(item) }}</code></p>
            <p class="original-text">{{ item.note }}</p>
            <dl><div><dt>{{ dueLabel(item) }}</dt><dd>{{ time(item.due_at,item.timezone) }}（{{ item.timezone }}）</dd></div>
              <div><dt>对象 / 请求人</dt><dd>{{ item.target==='self'?'self':`QQ ${item.target}` }} / {{ item.requester===null?'Bot 自主':`QQ ${item.requester}` }}</dd></div>
              <div><dt>创建</dt><dd>{{ time(item.created,item.timezone) }}</dd></div>
              <div v-if="item.delivered_at!==null"><dt>最近交付会话</dt><dd>{{ time(item.delivered_at,item.timezone) }} · 非 QQ 发送回执</dd></div>
              <div v-if="item.reason!==null"><dt>状态原因</dt><dd class="original-text">{{ item.reason }}</dd></div></dl>
            <v-btn v-if="['pending','blocked'].includes(item.status)" variant="outlined" color="error" :loading="cancelling===item.id" :disabled="cancelling!==null || creating || listLoading || stateLoading" @click="cancel(item)">取消此安排</v-btn>
          </li></ul>
          <v-btn v-if="nextOffset!==null" variant="outlined" :loading="listLoading" :disabled="listLoading || creating || cancelling!==null" @click="readList(true)">读取更多安排</v-btn>
        </template>
      </section>
    </template>
  </div>
</template>

<style scoped>
.host-schedules{max-width:1180px;margin-inline:auto}
.page-intro,.section-heading,.card-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.page-intro>div{min-width:0;flex:1 1 460px}.page-intro h1{margin:0 0 10px}
.eyebrow{font-size:12px;letter-spacing:.08em;color:var(--primary);font-weight:700;margin:0 0 5px}
.surface,.schedule-card{min-width:0;overflow-wrap:anywhere}.surface h2{font-size:18px;margin:0 0 12px}
.filters,.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px}
.form-grid{margin:16px 0}.filters{margin-bottom:12px}.host-schedules form>:last-child{margin-top:14px}
.schedule-list{list-style:none;padding:0;display:grid;gap:14px;margin:18px 0}
.schedule-card{border:1px solid var(--line);border-radius:10px;padding:16px}
.card-heading{font-weight:650}.original-text{white-space:pre-wrap;overflow-wrap:anywhere}
.schedule-card dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:10px;margin:16px 0}
.schedule-card dt{font-size:13px;color:var(--muted);font-weight:650}.schedule-card dd{margin:3px 0 0}
.cancel-operator{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.cancel-operator>.v-input{flex:1 1 260px;max-width:380px}
.success-note{border-left:3px solid var(--primary);background:var(--selected-bg);padding:8px 12px}
.host-schedules :deep(.v-btn){min-height:44px}.host-schedules :deep(.v-alert),.host-schedules .muted{overflow-wrap:anywhere}
@media(max-width:600px){.page-intro{display:grid}.page-intro>.v-btn{width:100%}.surface{padding:16px}.schedule-card{padding:14px}.cancel-operator>.v-input{max-width:none}}
</style>
