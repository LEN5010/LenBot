<script setup>
import { computed, ref, watch } from 'vue'
import { sceneName } from '../../../api.js'
import { browserApi } from '../../api/browser.js'
import { useAction, useResource } from '../../../composables/useResource.js'
import { notify, readPendingRestart } from '../../store.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import { confirm } from '../../../composables/useConfirm.js'
import ResourceState from '../../ui/ResourceState.vue'
import ErrorNote from '../../ui/ErrorNote.vue'
import ObjectList from '../../ui/ObjectList.vue'
import ObjectRow from '../../ui/ObjectRow.vue'
import StatusBadge from '../../ui/StatusBadge.vue'
import DevOnly from '../../ui/DevOnly.vue'

const emit = defineEmits(['dirty'])
const browser = useResource(() => browserApi.read())
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
  const result = await save.run(() => browserApi.save(draft.value))
  if (result) {
    browser.data.value = result
    readPendingRestart()
    notify('已保存，重启后生效')
  }
}
async function check() {
  const result = await act.run(async () => ({
    status: await browserApi.status(),
    devices: (await browserApi.devices()).devices,
  }))
  if (result) { live.value = result.status; devices.value = result.devices }
}
async function pair() {
  const result = await act.run(() => browserApi.pair())
  if (result) pairing.value = result.pairing_link
}
async function revoke(id) {
  if (!await confirm({ title: '取消这台设备的授权？', text: '它正在进行的浏览器操作会断开。', confirmLabel: '取消授权', danger: true })) return
  const result = await act.run(async () => {
    await browserApi.revoke(id)
    return (await browserApi.devices()).devices
  })
  if (result) { devices.value = result; notify('已取消授权') }
}
async function release(item) {
  if (!await confirm({ title: `关闭任务 #${item.id} 留下的浏览器会话？`, confirmLabel: '关闭会话' })) return
  const result = await act.run(() => browserApi.release({ scene: item.scene, task_id: item.id, session_id: session.value || null }))
  if (result) { await browser.reload(); notify('已关闭') }
}
const finished = status => ['done', 'failed', 'cancelled'].includes(status)
</script>

<template>
  <ResourceState :resource="browser" error-title="读取账号浏览器设置失败">
    <SettingSection title="账号浏览器" description="主人发起的任务可以使用专用账号浏览器。浏览器所在电脑需要安装对应扩展；远程传文件还需要文件助手。"
      :dirty="dirty" :problem="problem" :saving="save.busy.value" :error="save.error.value" @save="submit">
      <v-switch :model-value="draft !== null" label="启用账号浏览器"
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

      <template v-if="browser.data.value.running">
        <h3>连接与设备</h3>
        <div class="inline">
          <v-btn variant="tonal" :loading="act.busy.value" @click="check">查看连接和设备</v-btn>
          <v-btn variant="tonal" :disabled="act.busy.value" @click="pair">生成配对链接</v-btn>
        </div>
        <ErrorNote v-if="act.error.value" title="操作没有成功" :error="act.error.value" />
        <v-alert v-if="pairing" type="info" closable @click:close="pairing = ''">
          在专用浏览器的扩展里粘贴这个链接完成配对，只能用一次：<code class="pairing">{{ pairing }}</code></v-alert>
        <ObjectList v-if="devices" divided>
          <ObjectRow v-for="device in devices" :key="device.device_id" :title="device.label">
            <template #actions><v-btn size="small" variant="text" color="error" :disabled="act.busy.value" @click="revoke(device.device_id)">取消授权</v-btn></template>
          </ObjectRow>
          <li v-if="!devices.length" class="muted">还没有配对的设备</li>
        </ObjectList>
        <DevOnly v-if="live" label="守护进程状态" :json="live" />
      </template>

      <template v-if="browser.data.value.occupied.length">
        <h3>还占着浏览器的任务</h3>
        <ObjectList divided>
          <ObjectRow v-for="item in browser.data.value.occupied" :key="item.id" :title="item.goal" :subtitle="`${sceneName(item.scene)} · 任务 #${item.id}`">
            <template #meta><StatusBadge kind="task" :value="item.status" /></template>
            <template #actions><v-btn size="small" variant="text" :disabled="act.busy.value || !finished(item.status)" @click="release(item)">关闭会话</v-btn></template>
          </ObjectRow>
        </ObjectList>
        <DevOnly>
          <v-text-field v-model="session" label="要关闭的会话 ID" hint="任务没有记下会话时，从守护进程状态里找到并填写" persistent-hint />
        </DevOnly>
      </template>
    </SettingSection>
  </ResourceState>
</template>

<style scoped>
h3{margin-top:var(--sp-2)}
.pairing{display:block;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:var(--sp-1)}
</style>
