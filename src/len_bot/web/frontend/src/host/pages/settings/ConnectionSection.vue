<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../../api.js'
import { useAction } from '../../../composables/useResource.js'
import { confirm } from '../../../composables/useConfirm.js'
import { clone, numberOrBlank, same } from '../../forms.js'
import SettingSection from '../../ui/SettingSection.vue'
import AdvancedFields from '../../ui/AdvancedFields.vue'

const props = defineProps({ snapshot: { type: Object, required: true } })
const emit = defineEmits(['saved', 'dirty'])
const saved = computed(() => props.snapshot.saved.connection)
const draft = ref(null), token = ref('')
const save = useAction()

function onebotBody(value) {
  if (value === null) return null
  const common = {
    mode: value.mode, action_transport: value.action_transport,
    http_url: value.action_transport === 'http' ? value.http_url : null,
    request_timeout_seconds: value.request_timeout_seconds, ping_interval_seconds: value.ping_interval_seconds,
    ping_timeout_seconds: value.ping_timeout_seconds, max_frame_bytes: value.max_frame_bytes,
    upload_visible_root: value.upload_visible_root || null,
  }
  return value.mode === 'forward_ws' ? { ...common, ws_url: value.ws_url }
    : { ...common, listen_host: value.listen_host, listen_port: value.listen_port }
}
function body(value) {
  return { onebot: onebotBody(value.onebot), timezone: value.timezone, owners: value.owners,
    delivery: value.delivery, max_steps: value.max_steps, turn_timeout_seconds: value.turn_timeout_seconds,
    max_model_requests: value.max_model_requests, text_delivery: value.text_delivery }
}
watch(() => JSON.stringify(saved.value), () => { draft.value = clone(saved.value); token.value = '' }, { immediate: true })
const dirty = computed(() => token.value !== '' || !same(body(draft.value), body(saved.value)))
watch(dirty, value => emit('dirty', value), { immediate: true })

function changeMode(mode) {
  if (mode === null) {
    draft.value.onebot = null
    draft.value.delivery = 'simulated'
    token.value = ''
    return
  }
  if (draft.value.onebot === null) {
    draft.value.onebot = { action_transport: 'websocket', http_url: null,
      request_timeout_seconds: 10, ping_interval_seconds: 20, ping_timeout_seconds: 20,
      max_frame_bytes: 1048576, upload_visible_root: null }
  }
  const onebot = draft.value.onebot
  draft.value.onebot = mode === 'forward_ws'
    ? { ...onebot, mode, ws_url: onebot.ws_url ?? 'ws://127.0.0.1:3001' }
    : { ...onebot, mode, listen_host: onebot.listen_host ?? '127.0.0.1', listen_port: onebot.listen_port ?? 8080 }
}
async function submit() {
  if (draft.value.delivery === 'onebot' && saved.value.delivery !== 'onebot' && !await confirm({
    title: '改成真实发送？', text: '重启后 Bot 会在 QQ 群里真的发言。', confirmLabel: '保存', danger: true })) return
  const result = await save.run(() => api('/api/host/settings/connection', {
    method: 'PUT', body: JSON.stringify({ ...body(draft.value), access_token: token.value || null }),
  }))
  if (result) emit('saved', result)
}
</script>

<template>
  <SettingSection v-if="draft" title="连接 QQ" description="LenBot 通过 OneBot（例如 NapCat、SnowLuma）收发 QQ 消息。改动重启后生效。"
    :dirty="dirty" :saving="save.busy.value" :error="save.error.value" @save="submit">
    <p class="bot">Bot 的平台账号：<strong>{{ saved.bot_id }}</strong></p>
    <v-select :model-value="draft.onebot === null ? null : draft.onebot.mode" label="连接方式" :items="[
      { title: '不连接 OneBot（模拟面板）', value: null },
      { title: 'LenBot 去连 OneBot（正向 WebSocket）', value: 'forward_ws' },
      { title: '等 OneBot 来连 LenBot（反向 WebSocket）', value: 'reverse_ws' }]" @update:model-value="changeMode" />
    <template v-if="draft.onebot !== null">
    <v-text-field v-if="draft.onebot.mode === 'forward_ws'" v-model="draft.onebot.ws_url" label="OneBot 地址"
      hint="OneBot 里配置的 WebSocket 服务地址，例如 ws://127.0.0.1:3001" persistent-hint />
    <div v-else class="form-grid">
      <v-text-field v-model="draft.onebot.listen_host" label="监听地址" hint="OneBot 和 LenBot 在同一台机器上时填 127.0.0.1" persistent-hint />
      <v-text-field :model-value="draft.onebot.listen_port" type="number" label="监听端口" hint="和 OneBot 里填的反向地址端口一致" persistent-hint
        @update:model-value="value => draft.onebot.listen_port = numberOrBlank(value)" />
    </div>
    <v-text-field v-model="token" type="password" autocomplete="new-password" label="访问令牌"
      :placeholder="saved.onebot !== null && saved.onebot.access_token_configured ? '已设置，留空保持不变' : '未设置'"
      hint="和 OneBot 里配置的 token 一致；不需要就留空" persistent-hint />
    </template>
    <v-select v-model="draft.delivery" :disabled="draft.onebot === null" label="发送方式" :items="[
      { title: '真实发送到 QQ', value: 'onebot' }, { title: '模拟发送（只在面板里看到回复）', value: 'simulated' }]"
      hint="刚开始调试时用模拟发送，确认没问题再改成真实发送" persistent-hint />
    <div class="form-grid">
      <v-textarea :model-value="draft.owners.join('\n')" label="主人平台账号（每行一个）" rows="2"
        hint="例如 onebot:70001；留空表示没有主人" persistent-hint @update:model-value="value => draft.owners = value.split('\n').map(item => item.trim()).filter(Boolean)" />
      <v-text-field v-model="draft.timezone" label="时区" hint="影响时间显示、安静时段和提醒，例如 Asia/Shanghai" persistent-hint />
    </div>
    <AdvancedFields>
      <template v-if="draft.onebot !== null">
      <v-select v-model="draft.onebot.action_transport" label="发送通道" :items="[{ title: 'WebSocket', value: 'websocket' }, { title: 'HTTP', value: 'http' }]"
        hint="一般保持 WebSocket" persistent-hint />
      <v-text-field v-if="draft.onebot.action_transport === 'http'" v-model="draft.onebot.http_url" label="OneBot HTTP 地址" />
      <v-text-field :model-value="draft.onebot.request_timeout_seconds" type="number" label="操作超时（秒）" hint="等待 QQ 端完成一次操作的最长时间" persistent-hint
        @update:model-value="value => draft.onebot.request_timeout_seconds = numberOrBlank(value)" />
      <v-text-field :model-value="draft.onebot.ping_interval_seconds" type="number" label="心跳间隔（秒）" hint="多久检查一次连接是否还在" persistent-hint
        @update:model-value="value => draft.onebot.ping_interval_seconds = numberOrBlank(value)" />
      <v-text-field :model-value="draft.onebot.ping_timeout_seconds" type="number" label="心跳超时（秒）" hint="超过这个时间没有回应就算断开" persistent-hint
        @update:model-value="value => draft.onebot.ping_timeout_seconds = numberOrBlank(value)" />
      <v-text-field :model-value="draft.onebot.max_frame_bytes" type="number" label="单条数据上限（字节）" hint="收到的单条消息数据超过这个大小会被拒绝" persistent-hint
        @update:model-value="value => draft.onebot.max_frame_bytes = numberOrBlank(value)" />
      <v-text-field v-model="draft.onebot.upload_visible_root" label="OneBot 可见的文件目录" hint="发送任务生成的文件时使用，OneBot 那边看到的绝对路径；不发文件就留空" persistent-hint />
      </template>
      <v-text-field :model-value="draft.max_steps" type="number" label="每次回复最多步数" hint="Bot 一次回复里最多思考和调用工具几步" persistent-hint
        @update:model-value="value => draft.max_steps = numberOrBlank(value)" />
      <v-text-field :model-value="draft.turn_timeout_seconds" type="number" label="每次回复最长时间（秒）"
        @update:model-value="value => draft.turn_timeout_seconds = numberOrBlank(value)" />
      <v-text-field :model-value="draft.max_model_requests" type="number" label="同时进行的模型请求数" hint="所有群共用，越大越快，花费也可能更集中" persistent-hint
        @update:model-value="value => draft.max_model_requests = numberOrBlank(value)" />
      <v-text-field :model-value="draft.text_delivery.max_chars" type="number" label="每条消息最多字数" hint="长回复会按这个长度分成多条发送" persistent-hint
        @update:model-value="value => draft.text_delivery.max_chars = numberOrBlank(value)" />
      <v-text-field :model-value="draft.text_delivery.chars_per_second" type="number" label="打字速度（字／秒）" hint="决定分条之间等多久，模拟真人打字" persistent-hint
        @update:model-value="value => draft.text_delivery.chars_per_second = numberOrBlank(value)" />
      <v-text-field :model-value="draft.text_delivery.min_interval_seconds" type="number" label="分条最短间隔（秒）"
        @update:model-value="value => draft.text_delivery.min_interval_seconds = numberOrBlank(value)" />
      <v-text-field :model-value="draft.text_delivery.max_interval_seconds" type="number" label="分条最长间隔（秒）"
        @update:model-value="value => draft.text_delivery.max_interval_seconds = numberOrBlank(value)" />
    </AdvancedFields>
  </SettingSection>
</template>

<style scoped>
.bot{margin:0}
</style>
