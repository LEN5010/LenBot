<script setup>
import { computed, ref, watch } from 'vue'
import { api, sceneName } from '../../../api.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../components/SettingSection.vue'
import ErrorNote from '../../components/ErrorNote.vue'
import DevOnly from '../../components/DevOnly.vue'

const emit = defineEmits(['dirty'])
const browser = useResource(() => api('/api/host/browser'))
const save = useAction(), act = useAction()
const draft = ref(null), live = ref(null), devices = ref(null), pairing = ref(''), session = ref('')
const saved = computed(() => browser.data.value?.saved)
const blank = () => ({ socket: '', browser_instance_id: null, binary: '', home: '', timeout_seconds: 65, max_response_bytes: 16000000 })

watch(() => JSON.stringify(saved.value), () => { if (browser.data.value) draft.value = clone(saved.value) }, { immediate: true })
const dirty = computed(() => Boolean(browser.data.value) && !same(draft.value, saved.value))
watch(dirty, value => emit('dirty', value), { immediate: true })
const problem = computed(() => draft.value && [draft.value.socket, draft.value.binary, draft.value.home].some(item => !item.trim())
  ? '三个路径都要填写' : '')

async function submit() {
  const result = await save.run(() => api('/api/host/browser', { method: 'PUT', body: JSON.stringify({ settings: draft.value }) }))
  if (result) {
    browser.data.value = result
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
async function check() {
  const result = await act.run(async () => ({
    status: await api('/api/host/browser/status', { method: 'POST' }),
    devices: (await api('/api/host/browser/devices')).devices,
  }))
  if (result) { live.value = result.status; devices.value = result.devices }
}
async function pair() {
  const result = await act.run(() => api('/api/host/browser/pair', { method: 'POST' }))
  if (result) pairing.value = result.pairing_link
}
async function revoke(id) {
  if (!window.confirm('取消这台设备的授权？它正在进行的浏览器操作会断开。')) return
  const result = await act.run(async () => {
    await api(`/api/host/browser/devices/${encodeURIComponent(id)}`, { method: 'DELETE' })
    return (await api('/api/host/browser/devices')).devices
  })
  if (result) { devices.value = result; notify('已取消授权') }
}
async function release(item) {
  if (!window.confirm('关闭这个任务留下的浏览器会话？')) return
  const result = await act.run(() => api('/api/host/browser/release', {
    method: 'POST', body: JSON.stringify({ scene: item.scene, task_id: item.id, session_id: session.value || null }),
  }))
  if (result) { await browser.reload(); notify('已关闭') }
}
const finished = status => ['done', 'failed', 'cancelled'].includes(status)
</script>

<template>
  <ErrorNote v-if="browser.error.value" title="读取账号浏览器设置失败" :error="browser.error.value" />
  <template v-if="browser.data.value">
    <SettingSection title="账号浏览器" description="主人发起的任务可以用一个已登录账号的专用浏览器。需要先在本机部署浏览器守护进程和扩展。"
      :dirty="dirty" :problem="problem" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <v-switch :model-value="draft !== null" color="primary" label="启用账号浏览器" hide-details
        @update:model-value="value => draft = value ? (saved ? clone(saved) : blank()) : null" />
      <div v-if="draft" class="form-grid">
        <v-text-field v-model="draft.socket" label="守护进程 socket 路径" hint="完整路径" persistent-hint />
        <v-text-field v-model="draft.binary" label="浏览器命令行程序路径" hint="完整路径" persistent-hint />
        <v-text-field v-model="draft.home" label="守护进程 home 目录" hint="完整路径" persistent-hint />
        <v-text-field :model-value="draft.browser_instance_id ?? ''" label="浏览器实例 ID" hint="配对后从下方的连接状态里复制" persistent-hint
          @update:model-value="value => draft.browser_instance_id = value ? value : null" />
        <v-text-field :model-value="draft.timeout_seconds" type="number" label="单次操作超时（秒）"
          @update:model-value="value => draft.timeout_seconds = numberOrBlank(value)" />
        <v-text-field :model-value="draft.max_response_bytes" type="number" label="单次结果大小上限（字节）"
          @update:model-value="value => draft.max_response_bytes = numberOrBlank(value)" />
      </div>

    <div v-if="browser.data.value.running" class="browser-actions">
      <div class="row">
        <v-btn variant="outlined" :loading="act.busy.value" @click="check">查看连接和设备</v-btn>
        <v-btn variant="outlined" :disabled="act.busy.value" @click="pair">生成配对链接</v-btn>
      </div>
      <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
      <div v-if="pairing" class="pairing">
        <p>在专用浏览器的扩展里粘贴这个链接完成配对，只能用一次：</p>
        <code>{{ pairing }}</code>
        <v-btn size="small" variant="text" @click="pairing = ''">隐藏</v-btn>
      </div>
      <ul v-if="devices" class="devices">
        <li v-for="device in devices" :key="device.device_id">{{ device.label }}
          <v-btn size="small" variant="text" color="error" :disabled="act.busy.value" @click="revoke(device.device_id)">取消授权</v-btn></li>
        <li v-if="!devices.length" class="muted">还没有配对的设备</li>
      </ul>
      <DevOnly v-if="live" label="守护进程状态"><pre>{{ JSON.stringify(live, null, 2) }}</pre></DevOnly>
    </div>

    <div v-if="browser.data.value.occupied.length" class="occupied">
      <h3>还占着浏览器的任务</h3>
      <ul>
        <li v-for="item in browser.data.value.occupied" :key="item.id">
          <span>{{ sceneName(item.scene) }} · {{ item.goal }}</span>
          <v-btn size="small" variant="outlined" :disabled="act.busy.value || !finished(item.status)" @click="release(item)">关闭会话</v-btn>
        </li>
      </ul>
      <DevOnly>
        <v-text-field v-model="session" label="要关闭的会话 ID" hint="任务没有记下会话时，从守护进程状态里找到并填写" persistent-hint />
      </DevOnly>
    </div>
    </SettingSection>
  </template>
</template>

<style scoped>
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px}
.browser-actions,.occupied{display:grid;gap:10px}
.row{display:flex;gap:8px;flex-wrap:wrap}
.pairing code{display:block;white-space:pre-wrap;overflow-wrap:anywhere}
.devices,.occupied ul{list-style:none;margin:0;padding:0}
.devices li,.occupied li{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0}
h3{font-size:15px;margin:0}
</style>
